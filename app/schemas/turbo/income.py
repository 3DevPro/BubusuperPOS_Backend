from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class IncomeProfileResponse(BaseModel):
    window_days: int
    # Days within the window that have either a sale or an explicit
    # DailyClose — everything else is "no data yet", not zero income.
    days_recorded: int
    streak_days: int
    avg_daily_revenue: Decimal
    verified_avg_daily_revenue: Decimal
    cash_avg_daily_revenue: Decimal
    # verified / total across recorded days — 0 when nothing is recorded yet.
    verified_ratio: Decimal
    # credit-eligible average = verified in full + cash at CASH_CREDIT_WEIGHT
    # (see app/core/turbo_config.py) — what the credit tier is actually
    # justified by, shown so the tenant can see why their limit is what it is.
    credit_weighted_avg_daily_revenue: Decimal
    # Coefficient of variation (population stdev / mean) of recorded daily
    # revenue — 0 when fewer than 2 recorded days exist to compare.
    volatility: Decimal
    zero_days: list[date]
    credit_tier: str
    credit_limit: Decimal
    # Only set while the *next* tier's gate is streak-days (none -> tier_1);
    # tiers 2/3 gate on on_time_payments instead, which has no day countdown,
    # so this is None once tier_1 is reached — see next_tier_requirement for
    # a human-readable requirement that covers all tiers.
    next_tier_in_days: int | None
    # Paid installments (within grace) across every loan this tenant has
    # ever had — see app/services/turbo/credit_service.count_on_time_payments.
    on_time_payments: int
    # Human-readable description of what unlocks the next tier, e.g. "ผ่อน
    # ตรงเวลาอีก 2 งวดเพื่อขึ้นวงเงิน ฿30,000" — None once at the top tier.
    next_tier_requirement: str | None
