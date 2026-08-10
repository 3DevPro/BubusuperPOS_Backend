"""Seeds a demo tenant ("ร้านไก่ทอด") for the TURBO case pitch: 4 products
from the case's Product Ladder slide, ~27 days of realistic sales history
(~60% paid by QR), and a 3-day sick streak at the end of the window with
DailyClose(reason=sick) — the exact "3 consecutive days" setup the pitch's
auto-claim demo describes. A daily_income insurance policy is also seeded,
backdated before the sick streak, so the auto-claim banner has something to
show the moment the app is opened — buying a policy live in the demo can't
retroactively cover days that already happened, so this script pre-purchases
it the same way a shop that onboarded weeks ago would already have one.
A ฿10,000 motorcycle LoanAccount is also seeded with its first 4 installments
already paid on time and the 5th due in 5 days — so opening the app shows
tier_2 already unlocked (see app/core/turbo_config.TIER_2_ON_TIME_PAYMENTS)
and the "ครบกำหนดชำระ" reminder banner without any manual steps.

Safe to re-run: if the tenant already has sales, only the insurance policy
step is (re-)checked; nothing is duplicated.

Usage — this must run against the SAME database the app is actually serving
from. The simplest way is to run it inside the backend's own container,
which already has the right DATABASE_URL:

    docker exec infra-backend-1 python scripts/seed_demo.py

Running it standalone on the host works too, but only if DATABASE_URL is
set to match (the .env file's own DATABASE_URL for local/non-Docker runs
may point at a different database than what Docker Compose's backend
service actually uses — check BubusuperPOS_Infra/docker-compose.yml if
unsure):

    DATABASE_URL=postgresql+asyncpg://loyverse:loyverse@localhost:5433/loyverse \\
        python scripts/seed_demo.py
"""

import asyncio
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.core.db import async_session_factory  # noqa: E402
from app.core.security import hash_secret_async  # noqa: E402
from app.core.turbo_config import DAILY_BENEFIT_RATIO, DAILY_INCOME_PREMIUM_RATE, TIER_1_CREDIT_LIMIT  # noqa: E402
from app.models.product import Product  # noqa: E402
from app.models.sale import PaymentMethod, Sale, SaleItem, SaleStatus  # noqa: E402
from app.models.tenant import Tenant  # noqa: E402
from app.models.turbo.daily_close import DailyClose, DailyCloseReason  # noqa: E402
from app.models.turbo.insurance import InsurancePolicy, InsurancePolicyStatus, InsuranceProduct  # noqa: E402
from app.models.turbo.loan import (  # noqa: E402
    LoanAccount,
    LoanAccountStatus,
    LoanApplication,
    LoanApplicationStatus,
    LoanInstallment,
    LoanInstallmentStatus,
    LoanProduct,
)
from app.models.user import User, UserRole  # noqa: E402
from app.services.turbo.loan_service import _add_months, build_schedule  # noqa: E402

_TZ = ZoneInfo("Asia/Bangkok")
_CENTS = Decimal("0.01")

_OWNER_EMAIL = "test@test.cpm"
_OWNER_PASSWORD = "12345678"
_TENANT_NAME = "ร้านไก่ทอด"

# From the case's Product Ladder slide (สไลด์ 9).
_PRODUCT_SEED = [
    {"name": "ไก่ทอด", "sell_price": Decimal("20.00"), "cost_price": Decimal("10.00")},
    {"name": "น่องไก่", "sell_price": Decimal("25.00"), "cost_price": Decimal("13.00")},
    {"name": "ชุดใหญ่", "sell_price": Decimal("60.00"), "cost_price": Decimal("32.00")},
    {"name": "น้ำ", "sell_price": Decimal("10.00"), "cost_price": Decimal("4.00")},
]

_WINDOW_DAYS = 30
_SICK_DAYS = 3  # the most recent 3 days of the window
_QR_SHARE = 0.6

_LOAN_TERM_MONTHS = 12
_LOAN_PAID_INSTALLMENTS = 4  # >= TIER_2_ON_TIME_PAYMENTS, so tier_2 is already unlocked on open
_LOAN_NEXT_DUE_IN_DAYS = 5  # inside DUE_REMINDER_DAYS, so the reminder banner shows immediately


async def _get_or_create_tenant_and_owner(db) -> tuple[Tenant, User, bool]:
    """Returns (tenant, owner, already_existed)."""
    owner = await db.scalar(select(User).where(User.email == _OWNER_EMAIL))
    if owner is not None:
        tenant = await db.get(Tenant, owner.tenant_id)
        return tenant, owner, True

    tenant = Tenant(name=_TENANT_NAME)
    db.add(tenant)
    await db.flush()

    owner = User(
        tenant_id=tenant.id,
        name="เจ้าของร้านไก่ทอด",
        email=_OWNER_EMAIL,
        password_hash=await hash_secret_async(_OWNER_PASSWORD),
        role=UserRole.owner,
    )
    db.add(owner)
    await db.flush()
    return tenant, owner, False


async def _get_or_create_products(db, tenant: Tenant) -> list[Product]:
    existing = list(await db.scalars(select(Product).where(Product.tenant_id == tenant.id)))
    if existing:
        return existing

    products = [
        Product(
            tenant_id=tenant.id,
            name=p["name"],
            sell_price=p["sell_price"],
            cost_price=p["cost_price"],
            track_stock=False,
            stock_qty=0,
        )
        for p in _PRODUCT_SEED
    ]
    db.add_all(products)
    await db.flush()
    return products


async def _seed_sales_and_closes(db, tenant: Tenant, owner: User, products: list[Product]) -> Decimal:
    """Returns the average daily revenue across the non-sick days, used to
    underwrite the seeded insurance policy below with a realistic benefit."""
    today = datetime.now(_TZ).date()
    receipt_seq = 0
    total_revenue = Decimal("0")
    normal_days = 0

    for day_offset in range(_WINDOW_DAYS - 1, -1, -1):  # oldest to newest
        business_date = today - timedelta(days=day_offset)

        if day_offset < _SICK_DAYS:
            db.add(
                DailyClose(
                    tenant_id=tenant.id,
                    business_date=business_date,
                    closed_reason=DailyCloseReason.sick,
                    note="ไม่สบาย ร้านปิด",
                )
            )
            continue

        day_revenue = Decimal("0")
        for _ in range(random.randint(15, 28)):
            receipt_seq += 1
            created_at = datetime.combine(business_date, datetime.min.time(), tzinfo=_TZ).replace(
                hour=random.randint(9, 20), minute=random.randint(0, 59)
            )
            chosen = random.choices(products, k=random.randint(1, 3))
            payment_method = PaymentMethod.transfer_qr if random.random() < _QR_SHARE else PaymentMethod.cash

            sale_id = uuid.uuid4()
            subtotal = Decimal("0")
            items = []
            for product in chosen:
                qty = random.randint(1, 3)
                line_total = product.sell_price * qty
                subtotal += line_total
                items.append(
                    SaleItem(
                        tenant_id=tenant.id,
                        sale_id=sale_id,
                        product_id=product.id,
                        name_snapshot=product.name,
                        price_snapshot=product.sell_price,
                        cost_snapshot=product.cost_price,
                        qty=qty,
                        line_total=line_total,
                    )
                )

            db.add(
                Sale(
                    id=sale_id,
                    tenant_id=tenant.id,
                    receipt_no=f"DEMO-{receipt_seq:05d}",
                    user_id=owner.id,
                    client_uuid=uuid.uuid4(),
                    subtotal=subtotal,
                    discount=Decimal("0"),
                    total=subtotal,
                    payment_method=payment_method,
                    status=SaleStatus.completed,
                    created_at=created_at,
                )
            )
            db.add_all(items)
            day_revenue += subtotal

        # Closing every normal day too (not just the sick ones) mirrors real
        # usage — the owner taps "ปิดร้านวันนี้" daily — and is what gets the
        # streak to a full 30/30 immediately after seeding.
        db.add(DailyClose(tenant_id=tenant.id, business_date=business_date, closed_reason=DailyCloseReason.open))
        total_revenue += day_revenue
        normal_days += 1

    tenant.receipt_counter = receipt_seq
    return total_revenue / normal_days if normal_days else Decimal("0")


async def _seed_insurance_policy(db, tenant: Tenant, avg_daily_revenue: Decimal) -> bool:
    """Returns True if a new policy was created."""
    product = await db.scalar(select(InsuranceProduct).where(InsuranceProduct.code == "daily_income"))
    if product is None:
        print("  (ข้าม) ไม่พบสินค้าประกัน daily_income ในแคตตาล็อก — ต้องรัน migration ให้ครบก่อน")
        return False

    existing = await db.scalar(
        select(InsurancePolicy).where(
            InsurancePolicy.tenant_id == tenant.id,
            InsurancePolicy.product_id == product.id,
            InsurancePolicy.status == InsurancePolicyStatus.active,
        )
    )
    if existing is not None:
        return False

    daily_benefit = (avg_daily_revenue * DAILY_BENEFIT_RATIO).quantize(_CENTS, rounding=ROUND_HALF_UP)
    premium = (daily_benefit * DAILY_INCOME_PREMIUM_RATE).quantize(_CENTS, rounding=ROUND_HALF_UP)
    db.add(
        InsurancePolicy(
            tenant_id=tenant.id,
            product_id=product.id,
            daily_benefit=daily_benefit,
            premium_amount=premium,
            premium_cycle="daily",
            status=InsurancePolicyStatus.active,
            income_profile_snapshot={},
            # Well before the sick streak — a claim can never predate its
            # policy (see claim_service._eligible_closes), so this has to be
            # backdated past _WINDOW_DAYS for the seeded sick days to count.
            starts_at=datetime.now(_TZ) - timedelta(days=_WINDOW_DAYS + 10),
        )
    )
    return True


async def _next_account_number(db) -> str:
    # Mirrors loan_service._next_account_number's plain global-count scheme.
    count = await db.scalar(select(func.count()).select_from(LoanAccount))
    return f"TB-{(count or 0) + 1:06d}"


async def _seed_loan_account(db, tenant: Tenant) -> bool:
    """Returns True if a new loan account was created. Skips entirely if the
    tenant already has any loan account, same idempotency rule as
    _seed_insurance_policy."""
    existing = await db.scalar(select(LoanAccount).where(LoanAccount.tenant_id == tenant.id))
    if existing is not None:
        return False

    product = await db.scalar(select(LoanProduct).where(LoanProduct.code == "motorcycle"))
    if product is None:
        print("  (ข้าม) ไม่พบสินค้าสินเชื่อ motorcycle ในแคตตาล็อก — ต้องรัน migration ให้ครบก่อน")
        return False

    today = datetime.now(_TZ).date()
    next_due_date = today + timedelta(days=_LOAN_NEXT_DUE_IN_DAYS)
    # Installment #_LOAN_PAID_INSTALLMENTS+1's due date is next_due_date, so
    # installment #1's due date (what build_schedule anchors on) is that
    # many months earlier.
    first_due_date = _add_months(next_due_date, -_LOAN_PAID_INSTALLMENTS)

    principal = TIER_1_CREDIT_LIMIT  # ฿10,000 — what a fresh tier_1 streak alone unlocks
    schedule = build_schedule(principal, product.monthly_interest_rate, _LOAN_TERM_MONTHS, first_due_date)

    disbursed_at = datetime.combine(first_due_date, datetime.min.time(), tzinfo=timezone.utc) - timedelta(days=30)
    application = LoanApplication(
        tenant_id=tenant.id,
        product_id=product.id,
        requested_amount=principal,
        collateral_value=principal,
        term_months=_LOAN_TERM_MONTHS,
        approved_amount=principal,
        monthly_installment=schedule[0][4],
        monthly_interest_rate_snapshot=product.monthly_interest_rate,
        income_profile_snapshot={},
        credit_tier_snapshot="tier_1",
        cap_reasons=[],
        status=LoanApplicationStatus.disbursed,
        decided_at=disbursed_at,
    )
    db.add(application)
    await db.flush()

    account = LoanAccount(
        tenant_id=tenant.id,
        application_id=application.id,
        product_id=product.id,
        account_number=await _next_account_number(db),
        principal=principal,
        monthly_interest_rate=product.monthly_interest_rate,
        term_months=_LOAN_TERM_MONTHS,
        monthly_installment=schedule[0][4],
        status=LoanAccountStatus.active,
        disbursed_at=disbursed_at,
        first_due_date=first_due_date,
    )
    db.add(account)
    await db.flush()

    for seq, due_date, principal_component, interest_component, amount_due in schedule:
        paid = seq <= _LOAN_PAID_INSTALLMENTS
        db.add(
            LoanInstallment(
                tenant_id=tenant.id,
                account_id=account.id,
                sequence=seq,
                due_date=due_date,
                principal_component=principal_component,
                interest_component=interest_component,
                amount_due=amount_due,
                status=LoanInstallmentStatus.paid if paid else LoanInstallmentStatus.unpaid,
                paid_at=datetime.combine(due_date, datetime.min.time(), tzinfo=timezone.utc) if paid else None,
                paid_amount=amount_due if paid else None,
                paid_reference="seed-demo" if paid else None,
            )
        )
    return True


async def main() -> None:
    async with async_session_factory() as db:
        tenant, owner, already_existed = await _get_or_create_tenant_and_owner(db)
        products = await _get_or_create_products(db, tenant)

        if already_existed:
            sale_count = await db.scalar(select(func.count()).select_from(Sale).where(Sale.tenant_id == tenant.id))
        else:
            sale_count = 0

        if sale_count:
            print(f"ร้าน '{tenant.name}' มีข้อมูลขายอยู่แล้ว ({sale_count} รายการ) — ข้ามการสร้างยอดขายใหม่")
            avg_daily_revenue = await db.scalar(
                select(func.coalesce(func.sum(Sale.total), 0) / _WINDOW_DAYS).where(Sale.tenant_id == tenant.id)
            )
            avg_daily_revenue = Decimal(str(avg_daily_revenue or 0))
        else:
            print(f"กำลังสร้างข้อมูลย้อนหลัง {_WINDOW_DAYS} วันให้ร้าน '{tenant.name}'...")
            avg_daily_revenue = await _seed_sales_and_closes(db, tenant, owner, products)

        policy_created = await _seed_insurance_policy(db, tenant, avg_daily_revenue)
        loan_created = await _seed_loan_account(db, tenant)
        await db.commit()

    print()
    print("เสร็จแล้วครับ — ล็อกอินด้วย:")
    print(f"  อีเมล:     {_OWNER_EMAIL}")
    print(f"  รหัสผ่าน:  {_OWNER_PASSWORD}")
    print(f"  รายได้เฉลี่ยต่อวัน: {avg_daily_revenue:,.2f} บาท")
    print(f"  กรมธรรม์ชดเชยรายได้: {'สร้างใหม่' if policy_created else 'มีอยู่แล้ว/ไม่ได้สร้าง'}")
    print(f"  วันที่ร้านปิด (ป่วย) {_SICK_DAYS} วันล่าสุด — พร้อมให้เดโมเคลมอัตโนมัติ")
    print(
        f"  บัญชีสินเชื่อมอเตอร์ไซค์ ฿{TIER_1_CREDIT_LIMIT:,.0f}: "
        f"{'สร้างใหม่ (ผ่อนตรงเวลาแล้ว 4 งวด — tier_2 ปลดล็อก)' if loan_created else 'มีอยู่แล้ว/ไม่ได้สร้าง'}"
    )


if __name__ == "__main__":
    asyncio.run(main())
