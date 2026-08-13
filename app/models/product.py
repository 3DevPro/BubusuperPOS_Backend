import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db import Base
from app.models.base import TenantScopedMixin


class Category(TenantScopedMixin, Base):
    __tablename__ = "categories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Product(TenantScopedMixin, Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sku: Mapped[str | None] = mapped_column(String(64))
    barcode: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    category_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("categories.id"))
    image_url: Mapped[str | None] = mapped_column(String(1024))
    cost_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    sell_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    track_stock: Mapped[bool] = mapped_column(Boolean, default=True)
    stock_qty: Mapped[int] = mapped_column(Integer, default=0)
    low_stock_threshold: Mapped[int] = mapped_column(Integer, default=5)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BarcodeLookupCache(Base):
    """Result of an external barcode-lookup API call, keyed by barcode alone
    (deliberately NOT tenant-scoped) — the same product (e.g. a can of Coke)
    is scanned by many shops, so sharing this cache across every tenant is
    what keeps a free-tier lookup API's daily quota from being exhausted by
    duplicate calls. `found=False` rows are cached too: a barcode with no
    match is the case most likely to be re-scanned repeatedly (a local Thai
    product missing from these global databases), so it needs caching the
    most, not the least."""

    __tablename__ = "barcode_lookup_cache"

    barcode: Mapped[str] = mapped_column(String(64), primary_key=True)
    found: Mapped[bool] = mapped_column(Boolean, default=False)
    name: Mapped[str | None] = mapped_column(String(255))
    image_url: Mapped[str | None] = mapped_column(String(1024))
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(8))
    brand: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str | None] = mapped_column(String(32))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
