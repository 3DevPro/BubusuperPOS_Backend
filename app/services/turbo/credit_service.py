from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.core.tenancy import TenantContext
from app.core.turbo_config import (
    LOAN_LATE_GRACE_DAYS,
    STREAK_DAYS_REQUIRED,
    TIER_1_CREDIT_LIMIT,
    TIER_2_CREDIT_LIMIT,
    TIER_2_ON_TIME_PAYMENTS,
    TIER_3_CREDIT_LIMIT,
    TIER_3_ON_TIME_PAYMENTS,
)
from app.models.turbo.loan import LoanInstallment, LoanInstallmentStatus
from app.schemas.turbo.loan import CreditStandingResponse
from app.services.report_service import tenant_local_timezone


def resolve_tier(streak_days: int, on_time_payments: int) -> tuple[str, Decimal, int | None, str | None]:
    """Pure function, no DB access. streak_days gates tier 1 (a credit limit
    should grow from verified sales history, not a self-reported number);
    on_time_payments — installments paid on this app's own loans — gates
    tiers 2/3 on top of that, matching the case's "วงเงินโตตามประวัติผ่อน
    ไม่ใช่ตามยอดที่แจ้ง". Returns
    (credit_tier, credit_limit, next_tier_in_days, next_tier_requirement)."""
    if streak_days < STREAK_DAYS_REQUIRED:
        remaining = STREAK_DAYS_REQUIRED - streak_days
        return "none", Decimal("0"), remaining, f"บันทึกยอดขายต่อเนื่องอีก {remaining} วัน"
    if on_time_payments < TIER_2_ON_TIME_PAYMENTS:
        remaining = TIER_2_ON_TIME_PAYMENTS - on_time_payments
        req = f"ผ่อนตรงเวลาอีก {remaining} งวดเพื่อขึ้นวงเงิน ฿{TIER_2_CREDIT_LIMIT:,.0f}"
        return "tier_1", TIER_1_CREDIT_LIMIT, None, req
    if on_time_payments < TIER_3_ON_TIME_PAYMENTS:
        remaining = TIER_3_ON_TIME_PAYMENTS - on_time_payments
        req = f"ผ่อนตรงเวลาอีก {remaining} งวดเพื่อขึ้นวงเงิน ฿{TIER_3_CREDIT_LIMIT:,.0f}"
        return "tier_2", TIER_2_CREDIT_LIMIT, None, req
    return "tier_3", TIER_3_CREDIT_LIMIT, None, None


def is_on_time(paid_at: datetime, due_date: date, tz: ZoneInfo, grace_days: int = LOAN_LATE_GRACE_DAYS) -> bool:
    """paid_at is stored as a UTC instant; due_date is the tenant's local
    business date. Comparing paid_at's raw UTC .date() against due_date
    silently mis-scores anything paid near local midnight (Bangkok is
    UTC+7, so the local date can already be a day ahead of the UTC date) —
    always convert to the tenant's own timezone before taking .date()."""
    return paid_at.astimezone(tz).date() <= due_date + timedelta(days=grace_days)


async def count_on_time_payments(ctx: TenantContext) -> int:
    """Paid installments across every loan account this tenant has ever had —
    not just the currently active one, so closing one loan and opening
    another doesn't reset tier progress. "On time" allows
    LOAN_LATE_GRACE_DAYS of slack after due_date, same idea as a same-day
    bank queue not counting as a default."""
    tz = await tenant_local_timezone(ctx)
    installments = await ctx.db.scalars(
        ctx.scoped(LoanInstallment).where(LoanInstallment.status == LoanInstallmentStatus.paid)
    )
    return sum(1 for i in installments if i.paid_at is not None and is_on_time(i.paid_at, i.due_date, tz))


async def get_credit_standing(ctx: TenantContext) -> CreditStandingResponse:
    """Merges the income profile's tier fields with the raw on_time_payments
    count for a standalone /credit-standing view. Imports income_service
    lazily — income_service.get_income_profile() itself calls resolve_tier/
    count_on_time_payments from this module, so a module-level import here
    would be circular."""
    from app.services.turbo import income_service

    profile = await income_service.get_income_profile(ctx)
    return CreditStandingResponse(
        credit_tier=profile.credit_tier,
        credit_limit=profile.credit_limit,
        streak_days=profile.streak_days,
        on_time_payments=profile.on_time_payments,
        next_tier_in_days=profile.next_tier_in_days,
        next_tier_requirement=profile.next_tier_requirement,
    )
