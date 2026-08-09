import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db import Base
from app.models.base import TenantScopedMixin


class DailyCloseReason(str, enum.Enum):
    open = "open"
    sick = "sick"
    accident = "accident"
    holiday = "holiday"
    other = "other"


class DailyClose(TenantScopedMixin, Base):
    """One row per tenant per local business day the owner explicitly closes
    out — the only source of truth for *why* a day has zero sales. Turbo's
    income-certificate and auto-claim-detection logic (see
    app/services/turbo/*) both depend on this distinction: a zero-revenue day
    with no DailyClose row is "no data yet", while one with closed_reason in
    (sick, accident) is claim evidence."""

    __tablename__ = "turbo_daily_closes"
    __table_args__ = (UniqueConstraint("tenant_id", "business_date", name="uq_turbo_daily_closes_tenant_date"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # The tenant's own local calendar day (see report_service.tenant_local_timezone),
    # not a UTC timestamp — a close always refers to one wall-clock business day.
    business_date: Mapped[date] = mapped_column(Date)
    closed_reason: Mapped[DailyCloseReason] = mapped_column(default=DailyCloseReason.open)
    # Same-day expenses the POS has no other record of (e.g. cash paid for
    # ingredients) — kept separate from SaleItem.cost_snapshot so today's
    # net-profit picture in the income certificate doesn't require the owner
    # to have modeled every expense as a tracked product.
    extra_expense: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    note: Mapped[str | None] = mapped_column(String(500))
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
