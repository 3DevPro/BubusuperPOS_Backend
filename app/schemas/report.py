import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

ReportPeriod = Literal["today", "yesterday", "7d", "30d", "custom"]


class ReportSummary(BaseModel):
    period: ReportPeriod
    sale_count: int
    revenue: Decimal
    profit: Decimal
    item_count: int
    tax: Decimal


class DailyPoint(BaseModel):
    date: date
    sale_count: int
    revenue: Decimal


class DailyPaymentSplit(BaseModel):
    """Same local-day bucketing as DailyPoint, but broken out per
    Sale.payment_method — used by Turbo's income profile (see
    app/services/turbo/income_service.py) to tell verified (QR/card) income
    apart from self-reported cash. Empty dict means no sales that day, same
    "day exists but had none" meaning as DailyPoint.sale_count == 0."""

    date: date
    revenue_by_method: dict[str, Decimal]


class BestSeller(BaseModel):
    product_id: uuid.UUID
    name: str
    qty: int
    revenue: Decimal


class StaffSales(BaseModel):
    user_id: uuid.UUID
    name: str
    sale_count: int
    revenue: Decimal


class PaymentMethodSales(BaseModel):
    payment_method: str
    sale_count: int
    revenue: Decimal


class PeriodComparison(BaseModel):
    period_a: ReportPeriod
    period_b: ReportPeriod
    summary_a: ReportSummary
    summary_b: ReportSummary
    revenue_diff: Decimal
    revenue_diff_pct: Decimal | None
