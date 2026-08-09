import statistics
from datetime import date
from decimal import Decimal

from app.core.tenancy import TenantContext
from app.core.turbo_config import CASH_CREDIT_WEIGHT, STREAK_DAYS_REQUIRED, TIER_1_CREDIT_LIMIT
from app.schemas.turbo.income import IncomeProfileResponse
from app.services import report_service
from app.services.turbo import daily_close_service

_CASH_METHOD = "cash"


def _avg(values: list[Decimal]) -> Decimal:
    return (sum(values, Decimal("0")) / len(values)) if values else Decimal("0")


async def get_income_profile(ctx: TenantContext, days: int = 30) -> IncomeProfileResponse:
    points = await report_service.get_daily_series_by_payment_method(ctx, days)
    closes = await daily_close_service.list_closes(ctx, days)
    closed_dates = {c.business_date for c in closes}

    recorded_totals: list[Decimal] = []
    recorded_verified: list[Decimal] = []
    recorded_cash: list[Decimal] = []
    recorded_flags: list[bool] = []
    zero_days: list[date] = []

    for point in points:
        has_sales = bool(point.revenue_by_method)
        recorded = has_sales or point.date in closed_dates
        recorded_flags.append(recorded)
        if not recorded:
            continue

        cash = point.revenue_by_method.get(_CASH_METHOD, Decimal("0"))
        total = sum(point.revenue_by_method.values(), Decimal("0"))
        verified = total - cash

        recorded_totals.append(total)
        recorded_verified.append(verified)
        recorded_cash.append(cash)
        if total == 0:
            zero_days.append(point.date)

    # Streak = consecutive recorded days ending *today* — a gap anywhere
    # (including an unfinished "today" with no sales/close yet) stops it,
    # since points are oldest-first and we walk backward from the newest.
    streak_days = 0
    for recorded in reversed(recorded_flags):
        if not recorded:
            break
        streak_days += 1

    avg_daily_revenue = _avg(recorded_totals)
    verified_avg = _avg(recorded_verified)
    cash_avg = _avg(recorded_cash)
    credit_weighted_avg = verified_avg + cash_avg * CASH_CREDIT_WEIGHT

    total_revenue = sum(recorded_totals, Decimal("0"))
    total_verified = sum(recorded_verified, Decimal("0"))
    verified_ratio = (total_verified / total_revenue) if total_revenue > 0 else Decimal("0")

    volatility = Decimal("0")
    if len(recorded_totals) >= 2:
        floats = [float(v) for v in recorded_totals]
        mean = statistics.fmean(floats)
        if mean > 0:
            volatility = Decimal(str(round(statistics.pstdev(floats) / mean, 4)))

    eligible = streak_days >= STREAK_DAYS_REQUIRED
    credit_tier = "tier_1" if eligible else "none"
    credit_limit = TIER_1_CREDIT_LIMIT if eligible else Decimal("0")
    next_tier_in_days = None if eligible else max(0, STREAK_DAYS_REQUIRED - streak_days)

    return IncomeProfileResponse(
        window_days=days,
        days_recorded=len(recorded_totals),
        streak_days=streak_days,
        avg_daily_revenue=avg_daily_revenue,
        verified_avg_daily_revenue=verified_avg,
        cash_avg_daily_revenue=cash_avg,
        verified_ratio=verified_ratio,
        credit_weighted_avg_daily_revenue=credit_weighted_avg,
        volatility=volatility,
        zero_days=zero_days,
        credit_tier=credit_tier,
        credit_limit=credit_limit,
        next_tier_in_days=next_tier_in_days,
    )
