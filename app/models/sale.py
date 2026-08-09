import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db import Base
from app.models.base import TenantScopedMixin


class PaymentMethod(str, enum.Enum):
    cash = "cash"
    transfer_qr = "transfer_qr"
    card = "card"


class SaleStatus(str, enum.Enum):
    completed = "completed"
    void = "void"
    partially_refunded = "partially_refunded"
    refunded = "refunded"


class Sale(TenantScopedMixin, Base):
    __tablename__ = "sales"
    __table_args__ = (UniqueConstraint("tenant_id", "receipt_no", name="uq_sales_tenant_receipt_no"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    receipt_no: Mapped[str] = mapped_column(String(32))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    # Idempotency key generated client-side so retried/offline-queued checkouts
    # never create duplicate sales.
    client_uuid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id"), index=True
    )
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    discount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    # VAT snapshotted at sale time — a later change to the tenant's VAT rate
    # must never distort a historical receipt or report, same rationale as
    # SaleItem's price/cost snapshots below.
    tax: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    tax_rate_snapshot: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0, server_default="0")
    price_includes_tax_snapshot: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    payment_method: Mapped[PaymentMethod] = mapped_column(default=PaymentMethod.cash)
    status: Mapped[SaleStatus] = mapped_column(default=SaleStatus.completed)
    # Denormalized running total of refunds against this sale — kept in sync
    # with Refund/RefundItem in the same transaction, same pattern as
    # Product.stock_qty is kept in sync with StockMovement.
    refunded_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    # Loyalty points — kept separate from `discount`/`subtotal` on purpose so
    # refund_service's proportional refund math (which reads those two
    # fields) never claws back points value; a refund never adjusts points.
    points_earned: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    points_redeemed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    points_discount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SaleItem(TenantScopedMixin, Base):
    """price/cost/name are snapshotted at sale time so later catalog edits
    never distort historical revenue/profit reporting."""

    __tablename__ = "sale_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sale_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sales.id"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"))
    name_snapshot: Mapped[str] = mapped_column(String(255))
    price_snapshot: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    cost_snapshot: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    qty: Mapped[int] = mapped_column(Integer)
    # Flat currency discount applied to this line, before the sale-level
    # discount and before tax.
    discount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    # Denormalized running total — always written in the same transaction as
    # the RefundItem row that changes it, same pattern as Product.stock_qty.
    refunded_qty: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
