import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db import Base
from app.models.base import TenantScopedMixin


class Customer(TenantScopedMixin, Base):
    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("tenant_id", "phone", name="uq_customers_tenant_phone"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    # Nullable — a walk-in customer can be recorded by name alone. Unique per
    # tenant (not globally) since the same phone number may show up as a
    # different person's account across two unrelated shops.
    phone: Mapped[str | None] = mapped_column(String(20))
    points_balance: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
