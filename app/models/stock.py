import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db import Base
from app.models.base import TenantScopedMixin


class StockMovementType(str, enum.Enum):
    sale = "sale"
    purchase = "purchase"
    adjust = "adjust"
    waste = "waste"
    return_ = "return"


class StockMovement(TenantScopedMixin, Base):
    """Append-only ledger. Product.stock_qty is a denormalized running total
    updated in the same transaction as each insert here — never write one without the other."""

    __tablename__ = "stock_movements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"), index=True)
    type: Mapped[StockMovementType] = mapped_column()
    qty_delta: Mapped[int] = mapped_column(Integer)
    unit_cost: Mapped[float | None] = mapped_column(Numeric(12, 2))
    ref_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    note: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
