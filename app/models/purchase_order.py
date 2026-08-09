import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db import Base
from app.models.base import TenantScopedMixin


class PurchaseOrderStatus(str, enum.Enum):
    ordered = "ordered"
    partially_received = "partially_received"
    received = "received"
    cancelled = "cancelled"


class PurchaseOrder(TenantScopedMixin, Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (UniqueConstraint("tenant_id", "order_no", name="uq_purchase_orders_tenant_order_no"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_no: Mapped[str] = mapped_column(String(32))
    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    status: Mapped[PurchaseOrderStatus] = mapped_column(default=PurchaseOrderStatus.ordered)
    notes: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PurchaseOrderItem(TenantScopedMixin, Base):
    """name_snapshot/unit_cost are captured at order time, same rationale as
    SaleItem's snapshots — a later catalog rename/re-price must never distort
    a historical purchase order."""

    __tablename__ = "purchase_order_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_orders.id"), index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"))
    name_snapshot: Mapped[str] = mapped_column(String(255))
    qty_ordered: Mapped[int] = mapped_column(Integer)
    # Running total across possibly multiple partial receipts — same pattern
    # as SaleItem.refunded_qty.
    qty_received: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2))
