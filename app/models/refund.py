import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db import Base
from app.models.base import TenantScopedMixin


class Refund(TenantScopedMixin, Base):
    """Header row for one refund transaction against a Sale. A single customer
    return (full or partial) is one Refund with one-or-more RefundItem lines,
    mirroring how one Sale has many SaleItems."""

    __tablename__ = "refunds"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sale_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sales.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    # Idempotency key generated client-side, same rationale as Sale.client_uuid.
    client_uuid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, index=True)
    reason: Mapped[str | None] = mapped_column(String(255))
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    refund_tax: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RefundItem(TenantScopedMixin, Base):
    __tablename__ = "refund_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    refund_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("refunds.id"), index=True)
    sale_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sale_items.id"), index=True)
    qty: Mapped[int] = mapped_column(Integer)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
