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
    next_tier_in_days: int | None
