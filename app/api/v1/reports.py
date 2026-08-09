from datetime import date

from fastapi import APIRouter, Depends, Query

from app.core.deps import require
from app.core.permissions import Permission
from app.core.tenancy import TenantContext
from app.schemas.report import (
    BestSeller,
    DailyPoint,
    PaymentMethodSales,
    PeriodComparison,
    ReportPeriod,
    ReportSummary,
    StaffSales,
)
from app.services import report_service

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/summary", response_model=ReportSummary)
async def summary(
    period: ReportPeriod = "today",
    start_date: date | None = None,
    end_date: date | None = None,
    ctx: TenantContext = Depends(require(Permission.view_reports)),
) -> ReportSummary:
    return await report_service.get_summary(ctx, period, start_date, end_date)


@router.get("/daily", response_model=list[DailyPoint])
async def daily(
    days: int = Query(default=7, ge=1, le=90),
    ctx: TenantContext = Depends(require(Permission.view_reports)),
) -> list[DailyPoint]:
    return await report_service.get_daily_series(ctx, days)


@router.get("/best-sellers", response_model=list[BestSeller])
async def best_sellers(
    period: ReportPeriod = "today",
    limit: int = Query(default=5, ge=1, le=20),
    start_date: date | None = None,
    end_date: date | None = None,
    ctx: TenantContext = Depends(require(Permission.view_reports)),
) -> list[BestSeller]:
    return await report_service.get_best_sellers(ctx, period, limit, start_date, end_date)


@router.get("/worst-sellers", response_model=list[BestSeller])
async def worst_sellers(
    period: ReportPeriod = "today",
    limit: int = Query(default=5, ge=1, le=20),
    ctx: TenantContext = Depends(require(Permission.view_reports)),
) -> list[BestSeller]:
    return await report_service.get_worst_sellers(ctx, period, limit)


@router.get("/by-staff", response_model=list[StaffSales])
async def by_staff(
    period: ReportPeriod = "today",
    ctx: TenantContext = Depends(require(Permission.view_reports)),
) -> list[StaffSales]:
    return await report_service.get_sales_by_staff(ctx, period)


@router.get("/by-payment-method", response_model=list[PaymentMethodSales])
async def by_payment_method(
    period: ReportPeriod = "today",
    ctx: TenantContext = Depends(require(Permission.view_reports)),
) -> list[PaymentMethodSales]:
    return await report_service.get_sales_by_payment_method(ctx, period)


@router.get("/compare", response_model=PeriodComparison)
async def compare(
    period_a: ReportPeriod = "today",
    period_b: ReportPeriod = "yesterday",
    ctx: TenantContext = Depends(require(Permission.view_reports)),
) -> PeriodComparison:
    return await report_service.compare_periods(ctx, period_a, period_b)
