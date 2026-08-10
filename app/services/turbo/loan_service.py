import calendar
import uuid
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.tenancy import TenantContext
from app.core.turbo_config import LOAN_LTV
from app.models.tenant import Tenant
from app.models.turbo.branch import Lead, LeadSource
from app.models.turbo.loan import (
    LoanAccount,
    LoanAccountStatus,
    LoanApplication,
    LoanApplicationStatus,
    LoanInstallment,
    LoanInstallmentStatus,
    LoanProduct,
)
from app.models.user import User
from app.schemas.turbo.loan import (
    LoanAccountResponse,
    LoanAccountSummaryResponse,
    LoanInstallmentResponse,
    LoanQuoteResponse,
)
from app.services import audit_service
from app.services.report_service import tenant_local_timezone
from app.services.turbo import income_service
from app.services.turbo.branch_service import pick_branch_for_province
from app.services.turbo.credit_service import is_on_time
from app.services.turbo.daily_close_service import today_local

_CENTS = Decimal("0.01")


def _q(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def amortized_installment(principal: Decimal, monthly_rate: Decimal, term_months: int) -> Decimal:
    """Standard reducing-balance (ลดต้นลดดอก) formula: P·i / (1-(1+i)^-n),
    matching the "อัตราดอกเบี้ย 15-24% ต่อปี" turbo.co.th advertises. A 0%
    rate (never actually used by the seeded products, but keeps this
    general) falls back to an equal split."""
    if monthly_rate == 0:
        return _q(principal / term_months)
    factor = (Decimal(1) + monthly_rate) ** term_months
    installment = principal * monthly_rate * factor / (factor - Decimal(1))
    return _q(installment)


def build_schedule(
    principal: Decimal, monthly_rate: Decimal, term_months: int, first_due_date: date
) -> list[tuple[int, date, Decimal, Decimal, Decimal]]:
    """Full amortization schedule as (sequence, due_date, principal_component,
    interest_component, amount_due) tuples. The last installment absorbs
    whatever cents remain from per-period rounding, so
    sum(principal_component) always equals `principal` exactly rather than
    drifting by a few satang over a long term."""
    installment_amount = amortized_installment(principal, monthly_rate, term_months)
    schedule: list[tuple[int, date, Decimal, Decimal, Decimal]] = []
    remaining = principal
    for seq in range(1, term_months + 1):
        interest = _q(remaining * monthly_rate)
        if seq == term_months:
            principal_component = remaining
            amount_due = principal_component + interest
        else:
            principal_component = installment_amount - interest
            amount_due = installment_amount
        remaining -= principal_component
        due_date = _add_months(first_due_date, seq - 1)
        schedule.append((seq, due_date, principal_component, interest, amount_due))
    return schedule


async def _get_product(ctx: TenantContext, product_code: str) -> LoanProduct:
    product = await ctx.db.scalar(
        select(LoanProduct).where(LoanProduct.code == product_code, LoanProduct.is_active.is_(True))
    )
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "loan product not found")
    return product


async def list_products(ctx: TenantContext) -> list[LoanProduct]:
    result = await ctx.db.scalars(select(LoanProduct).where(LoanProduct.is_active.is_(True)).order_by(LoanProduct.code))
    return list(result)


async def quote(
    ctx: TenantContext,
    product_code: str,
    requested_amount: Decimal,
    collateral_value: Decimal,
    term_months: int,
) -> tuple[LoanProduct, LoanQuoteResponse, dict, str]:
    """Returns (product, quote, income_profile_snapshot, credit_tier) — apply()
    calls this and persists the same three alongside the application it
    creates, so a separate quote call and the application it leads to always
    agree on the numbers even though they're two independent requests (same
    pattern as insurance_service.quote)."""
    product = await _get_product(ctx, product_code)
    if not (product.min_term_months <= term_months <= product.max_term_months):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"จำนวนงวดต้องอยู่ระหว่าง {product.min_term_months}-{product.max_term_months} เดือน",
        )

    profile = await income_service.get_income_profile(ctx)
    if profile.credit_limit <= 0:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "ยังไม่ปลดล็อกวงเงิน — ต้องมียอดขายบันทึกต่อเนื่อง 30 วันก่อนยื่นขอสินเชื่อได้",
        )

    requested_amount = _q(requested_amount)
    ltv_ratio = LOAN_LTV[product.collateral_kind.value]
    ltv_cap = _q(collateral_value * ltv_ratio)

    candidates = {
        "credit": (profile.credit_limit, f"จำกัดที่ ฿{profile.credit_limit:,.0f} ตามระดับเครดิตปัจจุบัน"),
        "product": (
            product.max_principal,
            f"จำกัดที่ ฿{product.max_principal:,.0f} ตามเพดานสินเชื่อประเภทนี้",
        ),
        "collateral": (
            ltv_cap,
            f"จำกัดที่ ฿{ltv_cap:,.0f} ({int(ltv_ratio * 100)}% ของราคาประเมินหลักประกัน)",
        ),
    }
    binding_amount = min([requested_amount, *(v for v, _ in candidates.values())])
    if binding_amount >= requested_amount:
        approved_amount = requested_amount
        cap_reasons: list[str] = []
    else:
        approved_amount = binding_amount
        cap_reasons = [msg for v, msg in candidates.values() if v == approved_amount]

    schedule = build_schedule(approved_amount, product.monthly_interest_rate, term_months, date.today())
    total_repayment = sum((row[4] for row in schedule), Decimal("0"))
    total_interest = total_repayment - approved_amount
    monthly_installment = amortized_installment(approved_amount, product.monthly_interest_rate, term_months)

    quoted = LoanQuoteResponse(
        product_code=product.code,
        approved_amount=approved_amount,
        term_months=term_months,
        monthly_interest_rate=product.monthly_interest_rate,
        monthly_installment=monthly_installment,
        total_interest=total_interest,
        total_repayment=total_repayment,
        cap_reasons=cap_reasons,
    )
    return product, quoted, profile.model_dump(mode="json"), profile.credit_tier


async def apply(
    ctx: TenantContext,
    product_code: str,
    requested_amount: Decimal,
    collateral_value: Decimal,
    term_months: int,
) -> LoanApplication:
    product, quoted, snapshot, credit_tier = await quote(
        ctx, product_code, requested_amount, collateral_value, term_months
    )

    tenant = await ctx.db.get(Tenant, ctx.tenant_id)
    user = await ctx.db.get(User, ctx.user_id)
    # Tenant has no stored province (see app/models/tenant.py), so an in-app
    # application always falls back to a random branch — same as the public
    # O2O form does when no province is given.
    branch = await pick_branch_for_province(ctx.db, None)

    lead = Lead(
        assigned_branch_id=branch.id,
        source=LeadSource.in_app,
        name=tenant.name if tenant else "-",
        phone=user.phone if user else None,
        occupation=tenant.business_type if tenant else None,
        quoted_loan_amount=quoted.approved_amount,
        quoted_monthly_installment=quoted.monthly_installment,
        collateral_kind=product.collateral_kind.value,
    )
    ctx.db.add(lead)
    await ctx.db.flush()

    application = LoanApplication(
        tenant_id=ctx.tenant_id,
        product_id=product.id,
        requested_amount=_q(requested_amount),
        collateral_value=_q(collateral_value),
        term_months=term_months,
        approved_amount=quoted.approved_amount,
        monthly_installment=quoted.monthly_installment,
        monthly_interest_rate_snapshot=product.monthly_interest_rate,
        income_profile_snapshot=snapshot,
        credit_tier_snapshot=credit_tier,
        cap_reasons=quoted.cap_reasons,
        assigned_branch_id=branch.id,
        lead_id=lead.id,
        status=LoanApplicationStatus.submitted,
    )
    ctx.db.add(application)
    await audit_service.record(
        ctx, "loan.apply", f"ยื่นขอสินเชื่อ {product.name} วงเงิน {quoted.approved_amount} บาท"
    )
    await ctx.db.commit()
    await ctx.db.refresh(application)
    return application


async def list_applications(ctx: TenantContext) -> list[LoanApplication]:
    result = await ctx.db.scalars(ctx.scoped(LoanApplication).order_by(LoanApplication.created_at.desc()))
    return list(result)


async def _next_account_number(ctx: TenantContext) -> str:
    # A single global sequence across every tenant (accounts_number is
    # unique, not per-tenant) — good enough for a prototype's demo volume;
    # a real launch would want a DB sequence instead of a race-prone count.
    count = await ctx.db.scalar(select(func.count()).select_from(LoanAccount))
    return f"TB-{(count or 0) + 1:06d}"


async def disburse(ctx: TenantContext, application_id: uuid.UUID) -> LoanAccount:
    """Prototype-level auto-approve: no real e-KYC, credit-bureau check, or
    collateral appraisal happens here — apply()'s quote already re-derived
    the approved amount server-side, so this step only turns that decision
    into an account and writes every installment up front."""
    application = await ctx.db.scalar(
        ctx.scoped(LoanApplication).where(LoanApplication.id == application_id).with_for_update()
    )
    if application is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "loan application not found")
    if application.status == LoanApplicationStatus.disbursed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "สินเชื่อนี้เบิกจ่ายไปแล้ว")

    existing_active = await _active_account(ctx)
    if existing_active is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "มีบัญชีสินเชื่อที่ใช้งานอยู่แล้ว ไม่สามารถเบิกจ่ายสินเชื่อใหม่ซ้อนกันได้"
        )

    now = datetime.now(timezone.utc)
    first_due_date = _add_months(await today_local(ctx), 1)
    account = LoanAccount(
        tenant_id=ctx.tenant_id,
        application_id=application.id,
        product_id=application.product_id,
        account_number=await _next_account_number(ctx),
        principal=application.approved_amount,
        monthly_interest_rate=application.monthly_interest_rate_snapshot,
        term_months=application.term_months,
        monthly_installment=application.monthly_installment,
        first_due_date=first_due_date,
    )
    ctx.db.add(account)
    await ctx.db.flush()

    schedule = build_schedule(
        application.approved_amount, application.monthly_interest_rate_snapshot, application.term_months, first_due_date
    )
    for seq, due_date, principal_component, interest_component, amount_due in schedule:
        ctx.db.add(
            LoanInstallment(
                tenant_id=ctx.tenant_id,
                account_id=account.id,
                sequence=seq,
                due_date=due_date,
                principal_component=principal_component,
                interest_component=interest_component,
                amount_due=amount_due,
            )
        )

    application.status = LoanApplicationStatus.disbursed
    application.decided_at = now
    await audit_service.record(
        ctx, "loan.disburse", f"เบิกจ่ายสินเชื่อ {account.account_number} จำนวน {account.principal} บาท"
    )
    try:
        await ctx.db.commit()
    except IntegrityError:
        # Backstop for the DB uniqueness constraints (migration
        # c1a9f6d2e7b3) — the .with_for_update() lock above only serializes
        # concurrent calls for the *same* application; this catches two
        # different applications for the same tenant racing each other.
        await ctx.db.rollback()
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "มีบัญชีสินเชื่อที่ใช้งานอยู่แล้ว หรือสินเชื่อนี้เบิกจ่ายไปแล้ว ไม่สามารถเบิกจ่ายซ้ำได้",
        )
    await ctx.db.refresh(account)
    return account


async def _active_account(ctx: TenantContext) -> LoanAccount | None:
    return await ctx.db.scalar(
        ctx.scoped(LoanAccount)
        .where(LoanAccount.status == LoanAccountStatus.active)
        .order_by(LoanAccount.disbursed_at.desc())
    )


def to_installment_response(installment: LoanInstallment, today: date) -> LoanInstallmentResponse:
    # is_overdue has no backing column (see the model's docstring) — always
    # derived here from due_date, so it's built explicitly rather than via
    # from_attributes (which has nothing to read it from).
    is_overdue = installment.status == LoanInstallmentStatus.unpaid and installment.due_date < today
    return LoanInstallmentResponse(
        id=installment.id,
        account_id=installment.account_id,
        sequence=installment.sequence,
        due_date=installment.due_date,
        principal_component=installment.principal_component,
        interest_component=installment.interest_component,
        amount_due=installment.amount_due,
        status=installment.status.value,
        paid_at=installment.paid_at,
        paid_amount=installment.paid_amount,
        paid_reference=installment.paid_reference,
        is_overdue=is_overdue,
    )


async def get_account_summary(ctx: TenantContext) -> LoanAccountSummaryResponse | None:
    account = await _active_account(ctx)
    if account is None:
        return None

    installments = list(
        await ctx.db.scalars(
            ctx.scoped(LoanInstallment)
            .where(LoanInstallment.account_id == account.id)
            .order_by(LoanInstallment.sequence)
        )
    )
    paid = [i for i in installments if i.status == LoanInstallmentStatus.paid]
    unpaid = [i for i in installments if i.status == LoanInstallmentStatus.unpaid]
    tz = await tenant_local_timezone(ctx)
    today = datetime.now(tz).date()

    on_time_payments = sum(1 for i in paid if i.paid_at is not None and is_on_time(i.paid_at, i.due_date, tz))
    has_overdue = any(i.due_date < today for i in unpaid)
    outstanding = sum((i.amount_due for i in unpaid), Decimal("0"))

    next_installment = min(unpaid, key=lambda i: i.due_date) if unpaid else None

    return LoanAccountSummaryResponse(
        account=LoanAccountResponse.model_validate(account),
        outstanding_balance=outstanding,
        installments_total=len(installments),
        installments_paid=len(paid),
        on_time_payments=on_time_payments,
        next_due_date=next_installment.due_date if next_installment else None,
        next_due_amount=next_installment.amount_due if next_installment else None,
        due_in_days=(next_installment.due_date - today).days if next_installment else None,
        has_overdue=has_overdue,
    )


async def list_installments(ctx: TenantContext, account_id: uuid.UUID) -> list[LoanInstallmentResponse]:
    account = await ctx.db.scalar(ctx.scoped(LoanAccount).where(LoanAccount.id == account_id))
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "loan account not found")
    result = await ctx.db.scalars(
        ctx.scoped(LoanInstallment).where(LoanInstallment.account_id == account_id).order_by(LoanInstallment.sequence)
    )
    today = await today_local(ctx)
    return [to_installment_response(i, today) for i in result]


async def pay_installment(
    ctx: TenantContext, installment_id: uuid.UUID, amount: Decimal, reference: str | None
) -> LoanInstallmentResponse:
    """Records that the payer says they sent this via the shown QR — this
    prototype has no PromptPay settlement feed to reconcile against, so
    calling this endpoint is the only signal a payment happened. A real
    integration would verify against the bank's own webhook before marking
    an installment paid."""
    installment = await ctx.db.scalar(ctx.scoped(LoanInstallment).where(LoanInstallment.id == installment_id))
    if installment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "installment not found")
    if installment.status == LoanInstallmentStatus.paid:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "งวดนี้ชำระไปแล้ว")

    amount = _q(amount)
    if amount < installment.amount_due:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"ยอดชำระ ({amount}) น้อยกว่ายอดที่ต้องชำระ ({installment.amount_due}) บาท "
            "กรุณาชำระให้ครบตามจำนวนที่ระบุใน QR",
        )

    installment.status = LoanInstallmentStatus.paid
    installment.paid_at = datetime.now(timezone.utc)
    installment.paid_amount = amount
    installment.paid_reference = reference
    await audit_service.record(
        ctx, "loan.pay_installment", f"ชำระงวดที่ {installment.sequence} จำนวน {installment.paid_amount} บาท"
    )

    remaining_unpaid = await ctx.db.scalar(
        select(func.count())
        .select_from(LoanInstallment)
        .where(
            LoanInstallment.account_id == installment.account_id,
            LoanInstallment.status == LoanInstallmentStatus.unpaid,
        )
    )
    if remaining_unpaid == 0:
        account = await ctx.db.get(LoanAccount, installment.account_id)
        if account is not None:
            account.status = LoanAccountStatus.closed

    await ctx.db.commit()
    await ctx.db.refresh(installment)
    return to_installment_response(installment, await today_local(ctx))
