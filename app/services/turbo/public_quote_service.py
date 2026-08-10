from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.turbo_config import DAILY_INCOME_PREMIUM_RATE, LOAN_LTV
from app.models.turbo.branch import Lead, LeadSource
from app.models.turbo.loan import LoanProduct
from app.schemas.turbo.public import (
    LoanTermBoundsResponse,
    PublicLoanQuoteRequest,
    PublicLoanQuoteResponse,
    PublicQuoteRequest,
    PublicQuoteResponse,
)
from app.services.turbo.branch_service import pick_branch_for_province
from app.services.turbo.loan_service import amortized_installment, build_schedule

_CENTS = Decimal("0.01")
_DAYS_PER_MONTH = Decimal("30")


async def quote_and_create_lead(db: AsyncSession, body: PublicQuoteRequest) -> PublicQuoteResponse:
    """Quotes the daily_income product from the *budget* the visitor states —
    the reverse of the in-app quote (see insurance_service.quote), which
    starts from a tenant's actual sales. A prospect has no sales history yet,
    only what they say they can afford."""
    daily_premium = (body.monthly_budget / _DAYS_PER_MONTH).quantize(_CENTS, rounding=ROUND_HALF_UP)
    daily_benefit = (daily_premium / DAILY_INCOME_PREMIUM_RATE).quantize(_CENTS, rounding=ROUND_HALF_UP)

    branch = await pick_branch_for_province(db, body.province)

    lead = Lead(
        assigned_branch_id=branch.id,
        source=LeadSource.o2o_web,
        name=body.name,
        phone=body.phone,
        occupation=body.occupation,
        age=body.age,
        quoted_daily_benefit=daily_benefit,
        quoted_premium=daily_premium,
    )
    db.add(lead)
    await db.commit()
    await db.refresh(lead)

    return PublicQuoteResponse(daily_benefit=daily_benefit, premium_amount=daily_premium, lead_id=lead.id)


async def quote_loan_and_create_lead(db: AsyncSession, body: PublicLoanQuoteRequest) -> PublicLoanQuoteResponse:
    """Loan counterpart of quote_and_create_lead above — a prospect has no
    income profile yet, so approved_amount is capped only by the product's
    own ceiling and the stated collateral's loan-to-value, never by a credit
    tier the way the in-app loan_service.quote is (a prospect has no tier
    yet either)."""
    product = await db.scalar(
        select(LoanProduct).where(
            LoanProduct.collateral_kind == body.collateral_kind, LoanProduct.is_active.is_(True)
        )
    )
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "loan product not found for this collateral kind")
    if not (product.min_term_months <= body.term_months <= product.max_term_months):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"จำนวนงวดต้องอยู่ระหว่าง {product.min_term_months}-{product.max_term_months} เดือน",
        )

    ltv_cap = (body.collateral_value * LOAN_LTV[body.collateral_kind.value]).quantize(
        _CENTS, rounding=ROUND_HALF_UP
    )
    approved_amount = min(
        body.requested_amount.quantize(_CENTS, rounding=ROUND_HALF_UP), product.max_principal, ltv_cap
    )

    schedule = build_schedule(approved_amount, product.monthly_interest_rate, body.term_months, date.today())
    total_repayment = sum((row[4] for row in schedule), Decimal("0"))
    total_interest = total_repayment - approved_amount
    monthly_installment = amortized_installment(approved_amount, product.monthly_interest_rate, body.term_months)

    branch = await pick_branch_for_province(db, body.province)
    lead = Lead(
        assigned_branch_id=branch.id,
        source=LeadSource.o2o_web,
        name=body.name,
        phone=body.phone,
        occupation=body.occupation,
        age=body.age,
        quoted_loan_amount=approved_amount,
        quoted_monthly_installment=monthly_installment,
        collateral_kind=body.collateral_kind.value,
    )
    db.add(lead)
    await db.commit()
    await db.refresh(lead)

    return PublicLoanQuoteResponse(
        approved_amount=approved_amount,
        term_months=body.term_months,
        monthly_interest_rate=product.monthly_interest_rate,
        monthly_installment=monthly_installment,
        total_interest=total_interest,
        total_repayment=total_repayment,
        lead_id=lead.id,
    )


async def loan_term_bounds(db: AsyncSession) -> list[LoanTermBoundsResponse]:
    """Per-collateral-kind term-slider bounds for the public loan-quote form.
    Can't reuse the authenticated loan_service.list_products (gated on
    Permission.manage_loans) since this form is anonymous — this is a
    narrow, read-only projection of the same turbo_loan_products catalog."""
    products = await db.scalars(select(LoanProduct).where(LoanProduct.is_active.is_(True)))
    return [
        LoanTermBoundsResponse(
            collateral_kind=p.collateral_kind.value,
            min_term_months=p.min_term_months,
            max_term_months=p.max_term_months,
        )
        for p in products
    ]
