from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import func, select

from app.core.tenancy import TenantContext
from app.models.product import Product
from app.models.sale import Sale, SaleItem, SaleStatus
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.report import (
    BestSeller,
    DailyPaymentSplit,
    DailyPoint,
    PaymentMethodSales,
    PeriodComparison,
    ReportPeriod,
    ReportSummary,
    StaffSales,
)

_UTC = ZoneInfo("UTC")

# A partially-refunded sale is still (mostly) revenue and must stay in every
# report query below — only a *fully* refunded sale drops out, and its
# balance is already zero so excluding it changes nothing either way.
_REPORTABLE_STATUSES = (SaleStatus.completed, SaleStatus.partially_refunded)

# Each SaleItem's remaining (non-refunded) quantity and its proportional
# share of line_total — reused by every query that aggregates by product so
# a refunded unit stops counting as sold revenue/profit.
_REMAINING_QTY = SaleItem.qty - SaleItem.refunded_qty
_REMAINING_LINE_TOTAL = SaleItem.line_total * _REMAINING_QTY / SaleItem.qty


async def tenant_local_timezone(ctx: TenantContext) -> ZoneInfo:
    """Public — reused outside this module (see app/services/turbo/daily_close_service.py)
    by anything else that needs to reason about the tenant's own business day
    instead of recomputing this lookup."""
    tz_name = await ctx.db.scalar(select(Tenant.timezone).where(Tenant.id == ctx.tenant_id))
    return ZoneInfo(tz_name or "Asia/Bangkok")


async def local_date_range_to_utc(ctx: TenantContext, start_date: date, end_date: date) -> tuple[datetime, datetime]:
    """start_date/end_date are calendar days in the tenant's own timezone
    (e.g. from a date-range picker), inclusive of end_date — converts to a
    half-open UTC range for filtering Sale.created_at. Shared by reports'
    "custom" period and the sales list endpoint's date filter, so both agree
    on what a given date range actually means.
    """
    if start_date > end_date:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "start_date must not be after end_date")
    tz = await tenant_local_timezone(ctx)
    start_local = datetime.combine(start_date, datetime.min.time(), tzinfo=tz)
    end_local = datetime.combine(end_date, datetime.min.time(), tzinfo=tz) + timedelta(days=1)
    return start_local.astimezone(_UTC), end_local.astimezone(_UTC)


async def _period_bounds_utc(
    ctx: TenantContext,
    period: ReportPeriod,
    start_date: date | None = None,
    end_date: date | None = None,
) -> tuple[datetime, datetime]:
    # "Today" must mean the tenant's local day, not the UTC day — a sale at
    # 01:00 Bangkok time is 18:00 UTC the day before, and would silently land
    # in "yesterday" if we didn't shift by the store's own timezone.
    if period == "custom":
        if start_date is None or end_date is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "start_date and end_date are required for a custom period")
        return await local_date_range_to_utc(ctx, start_date, end_date)

    tz = await tenant_local_timezone(ctx)
    today_start_local = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)

    if period == "today":
        start_local, end_local = today_start_local, today_start_local + timedelta(days=1)
    elif period == "yesterday":
        start_local, end_local = today_start_local - timedelta(days=1), today_start_local
    elif period == "7d":
        start_local, end_local = today_start_local - timedelta(days=6), today_start_local + timedelta(days=1)
    else:  # 30d
        start_local, end_local = today_start_local - timedelta(days=29), today_start_local + timedelta(days=1)

    return start_local.astimezone(_UTC), end_local.astimezone(_UTC)


async def get_summary(
    ctx: TenantContext, period: ReportPeriod, start_date: date | None = None, end_date: date | None = None
) -> ReportSummary:
    start_utc, end_utc = await _period_bounds_utc(ctx, period, start_date, end_date)

    sale_count, revenue, tax = (
        await ctx.db.execute(
            select(
                func.count(Sale.id),
                func.coalesce(func.sum(Sale.total - Sale.refunded_total), 0),
                func.coalesce(func.sum(Sale.tax), 0),
            ).where(
                Sale.tenant_id == ctx.tenant_id,
                Sale.status.in_(_REPORTABLE_STATUSES),
                Sale.created_at >= start_utc,
                Sale.created_at < end_utc,
            )
        )
    ).one()

    profit, item_count = (
        await ctx.db.execute(
            select(
                # Revenue side must be _REMAINING_LINE_TOTAL, not
                # price_snapshot * qty — otherwise a cashier's per-item
                # discount (or a promotion discount) is invisible to profit
                # and a free/heavily-discounted line reports full margin.
                # round() to 2dp because _REMAINING_LINE_TOTAL divides by
                # qty, which Postgres NUMERIC division widens to many extra
                # decimal digits even though money is always 2dp.
                func.coalesce(func.round(func.sum(_REMAINING_LINE_TOTAL - SaleItem.cost_snapshot * _REMAINING_QTY), 2), 0),
                func.coalesce(func.sum(_REMAINING_QTY), 0),
            )
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(
                Sale.tenant_id == ctx.tenant_id,
                Sale.status.in_(_REPORTABLE_STATUSES),
                Sale.created_at >= start_utc,
                Sale.created_at < end_utc,
            )
        )
    ).one()

    return ReportSummary(
        period=period,
        sale_count=sale_count or 0,
        revenue=revenue or Decimal("0"),
        profit=profit or Decimal("0"),
        item_count=item_count or 0,
        tax=tax or Decimal("0"),
    )


async def get_daily_series(ctx: TenantContext, days: int = 7) -> list[DailyPoint]:
    tz = await tenant_local_timezone(ctx)
    end_local = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    start_local = end_local - timedelta(days=days)
    start_utc, end_utc = start_local.astimezone(_UTC), end_local.astimezone(_UTC)

    # Bucket by the tenant's *local* calendar day, not UTC — same reasoning
    # as get_summary(). `timezone(name, ts)` converts a timestamptz into the
    # naive local wall-clock time in that zone; date() truncates it to a day.
    local_day = func.date(func.timezone(tz.key, Sale.created_at))

    rows = (
        await ctx.db.execute(
            select(
                local_day.label("day"),
                func.count(Sale.id),
                func.coalesce(func.sum(Sale.total - Sale.refunded_total), 0),
            )
            .where(
                Sale.tenant_id == ctx.tenant_id,
                Sale.status.in_(_REPORTABLE_STATUSES),
                Sale.created_at >= start_utc,
                Sale.created_at < end_utc,
            )
            .group_by(local_day)
        )
    ).all()
    by_day = {row.day: (row[1], row[2]) for row in rows}

    points = []
    for i in range(days):
        day = (start_local + timedelta(days=i)).date()
        sale_count, revenue = by_day.get(day, (0, Decimal("0")))
        points.append(DailyPoint(date=day, sale_count=sale_count, revenue=revenue))
    return points


async def get_daily_series_by_payment_method(ctx: TenantContext, days: int = 30) -> list[DailyPaymentSplit]:
    """Same local-day window and bucketing as get_daily_series, but grouped
    by Sale.payment_method too — a QR/card sale lands in a bank record the
    tenant didn't type in themselves, a cash sale doesn't, and that's the
    verified-vs-self-reported split Turbo's income profile is built on (see
    app/services/turbo/income_service.py)."""
    tz = await tenant_local_timezone(ctx)
    end_local = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    start_local = end_local - timedelta(days=days)
    start_utc, end_utc = start_local.astimezone(_UTC), end_local.astimezone(_UTC)

    local_day = func.date(func.timezone(tz.key, Sale.created_at))

    rows = (
        await ctx.db.execute(
            select(
                local_day.label("day"),
                Sale.payment_method,
                func.coalesce(func.sum(Sale.total - Sale.refunded_total), 0),
            )
            .where(
                Sale.tenant_id == ctx.tenant_id,
                Sale.status.in_(_REPORTABLE_STATUSES),
                Sale.created_at >= start_utc,
                Sale.created_at < end_utc,
            )
            .group_by(local_day, Sale.payment_method)
        )
    ).all()

    by_day: dict[date, dict[str, Decimal]] = {}
    for day, method, revenue in rows:
        by_day.setdefault(day, {})[method.value] = revenue

    points = []
    for i in range(days):
        day = (start_local + timedelta(days=i)).date()
        points.append(DailyPaymentSplit(date=day, revenue_by_method=by_day.get(day, {})))
    return points


async def get_best_sellers(
    ctx: TenantContext,
    period: ReportPeriod,
    limit: int = 5,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[BestSeller]:
    start_utc, end_utc = await _period_bounds_utc(ctx, period, start_date, end_date)

    rows = (
        await ctx.db.execute(
            select(
                SaleItem.product_id,
                func.max(SaleItem.name_snapshot).label("name"),
                func.sum(_REMAINING_QTY).label("qty"),
                func.sum(_REMAINING_LINE_TOTAL).label("revenue"),
            )
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(
                Sale.tenant_id == ctx.tenant_id,
                Sale.status.in_(_REPORTABLE_STATUSES),
                Sale.created_at >= start_utc,
                Sale.created_at < end_utc,
            )
            .group_by(SaleItem.product_id)
            .order_by(func.sum(_REMAINING_QTY).desc())
            .limit(limit)
        )
    ).all()

    return [
        BestSeller(product_id=row.product_id, name=row.name, qty=row.qty, revenue=row.revenue) for row in rows
    ]


async def get_sales_by_staff(ctx: TenantContext, period: ReportPeriod) -> list[StaffSales]:
    start_utc, end_utc = await _period_bounds_utc(ctx, period)

    rows = (
        await ctx.db.execute(
            select(
                Sale.user_id,
                func.max(User.name).label("name"),
                func.count(Sale.id).label("sale_count"),
                func.coalesce(func.sum(Sale.total - Sale.refunded_total), 0).label("revenue"),
            )
            .join(User, User.id == Sale.user_id)
            .where(
                Sale.tenant_id == ctx.tenant_id,
                Sale.status.in_(_REPORTABLE_STATUSES),
                Sale.created_at >= start_utc,
                Sale.created_at < end_utc,
            )
            .group_by(Sale.user_id)
            .order_by(func.coalesce(func.sum(Sale.total - Sale.refunded_total), 0).desc())
        )
    ).all()

    return [
        StaffSales(user_id=row.user_id, name=row.name, sale_count=row.sale_count, revenue=row.revenue)
        for row in rows
    ]


async def get_sales_by_payment_method(ctx: TenantContext, period: ReportPeriod) -> list[PaymentMethodSales]:
    start_utc, end_utc = await _period_bounds_utc(ctx, period)

    rows = (
        await ctx.db.execute(
            select(
                Sale.payment_method,
                func.count(Sale.id).label("sale_count"),
                func.coalesce(func.sum(Sale.total - Sale.refunded_total), 0).label("revenue"),
            )
            .where(
                Sale.tenant_id == ctx.tenant_id,
                Sale.status.in_(_REPORTABLE_STATUSES),
                Sale.created_at >= start_utc,
                Sale.created_at < end_utc,
            )
            .group_by(Sale.payment_method)
            .order_by(func.coalesce(func.sum(Sale.total - Sale.refunded_total), 0).desc())
        )
    ).all()

    return [
        PaymentMethodSales(payment_method=row.payment_method.value, sale_count=row.sale_count, revenue=row.revenue)
        for row in rows
    ]


async def compare_periods(ctx: TenantContext, period_a: ReportPeriod, period_b: ReportPeriod) -> PeriodComparison:
    summary_a = await get_summary(ctx, period_a)
    summary_b = await get_summary(ctx, period_b)
    revenue_diff = summary_a.revenue - summary_b.revenue
    revenue_diff_pct = (revenue_diff / summary_b.revenue * 100) if summary_b.revenue != 0 else None
    return PeriodComparison(
        period_a=period_a,
        period_b=period_b,
        summary_a=summary_a,
        summary_b=summary_b,
        revenue_diff=revenue_diff,
        revenue_diff_pct=revenue_diff_pct,
    )


async def get_worst_sellers(ctx: TenantContext, period: ReportPeriod, limit: int = 5) -> list[BestSeller]:
    """Unlike get_best_sellers, this starts from Product (not SaleItem) via a
    LEFT JOIN, so a product with zero sales in the period — the actual
    "not moving" case shop owners care about — still shows up with qty=0
    instead of being silently absent from an aggregate-only query."""
    start_utc, end_utc = await _period_bounds_utc(ctx, period)

    sale_agg = (
        select(
            SaleItem.product_id.label("product_id"),
            func.sum(_REMAINING_QTY).label("qty"),
            func.sum(_REMAINING_LINE_TOTAL).label("revenue"),
        )
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(
            Sale.tenant_id == ctx.tenant_id,
            Sale.status.in_(_REPORTABLE_STATUSES),
            Sale.created_at >= start_utc,
            Sale.created_at < end_utc,
        )
        .group_by(SaleItem.product_id)
        .subquery()
    )

    qty = func.coalesce(sale_agg.c.qty, 0)
    revenue = func.coalesce(sale_agg.c.revenue, 0)

    rows = (
        await ctx.db.execute(
            select(Product.id, Product.name, qty.label("qty"), revenue.label("revenue"))
            .outerjoin(sale_agg, sale_agg.c.product_id == Product.id)
            .where(Product.tenant_id == ctx.tenant_id, Product.is_active.is_(True))
            .order_by(qty.asc())
            .limit(limit)
        )
    ).all()

    return [BestSeller(product_id=row.id, name=row.name, qty=row.qty, revenue=row.revenue) for row in rows]
