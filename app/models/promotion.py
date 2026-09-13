import enum
import uuid
from datetime import datetime, time
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Time, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db import Base
from app.models.base import TenantScopedMixin


class PromotionKind(str, enum.Enum):
    percent_off = "percent_off"
    amount_off = "amount_off"
    buy_n_get_m = "buy_n_get_m"
    buy_n_for_price = "buy_n_for_price"


class PromotionScope(str, enum.Enum):
    product = "product"
    category = "category"
    all = "all"


class Promotion(TenantScopedMixin, Base):
    """A rule the checkout-time promotion_engine resolves against the cart —
    see app/services/promotion_engine.py for how kind/scope/coupon_code
    combine. Only the fields relevant to `kind` are populated; the rest stay
    null (e.g. a percent_off promo never sets buy_qty/get_qty)."""

    __tablename__ = "promotions"
    __table_args__ = (UniqueConstraint("tenant_id", "coupon_code", name="uq_promotions_tenant_coupon_code"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[PromotionKind] = mapped_column()
    scope: Mapped[PromotionScope] = mapped_column(default=PromotionScope.product)
    percent_off: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    amount_off: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    buy_qty: Mapped[int | None] = mapped_column(Integer)
    get_qty: Mapped[int | None] = mapped_column(Integer)
    bundle_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # Null = auto-applies to every matching cart; set = only applies when the
    # cashier/customer enters this code at checkout.
    coupon_code: Mapped[str | None] = mapped_column(String(32))
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # "1111100" = Mon-Fri, index 0 = Monday — null means every day.
    active_weekdays: Mapped[str | None] = mapped_column(String(7))
    daily_start_time: Mapped[time | None] = mapped_column(Time)
    daily_end_time: Mapped[time | None] = mapped_column(Time)
    max_uses: Mapped[int | None] = mapped_column(Integer)
    uses_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Lower priority wins when more than one auto-apply promotion matches
    # the same line — see promotion_engine.resolve.
    priority: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    # A stackable coupon may apply on top of one auto-applied promotion;
    # nothing else stacks (see promotion_engine module docstring).
    stackable: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PromotionTarget(TenantScopedMixin, Base):
    """One row per (product or category) a promotion applies to — lets a
    single promotion cover several specific products (e.g. "ลด 10% กาแฟทุก
    ชนิด" across 5 coffee SKUs) without needing scope=all. Unused when
    scope=all, which applies to every product with no target rows."""

    __tablename__ = "promotion_targets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    promotion_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("promotions.id"), index=True)
    product_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"))
    category_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("categories.id"))
