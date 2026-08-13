import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    business_type: Mapped[str | None] = mapped_column(String(100))
    currency: Mapped[str] = mapped_column(String(3), default="THB")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Bangkok")
    # Incremented in the same transaction as each sale to hand out gap-free
    # receipt numbers even when two cashiers check out at once — Postgres
    # row-locks the UPDATE, so no separate locking is needed.
    receipt_counter: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Same gap-free-numbering rationale as receipt_counter, for purchase
    # orders instead of sales.
    po_counter: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Same gap-free-numbering rationale as receipt_counter, for in-store
    # EAN-13 barcodes generated for products that don't have a manufacturer
    # barcode (see app/services/barcode_service.py).
    internal_barcode_counter: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Thai mobile number (10 digits) or citizen/tax ID (13 digits) used to
    # build PromptPay QR payloads at checkout. Null until the owner sets it.
    promptpay_id: Mapped[str | None] = mapped_column(String(20))
    # Abbreviated tax invoice (ใบกำกับภาษีอย่างย่อ) fields — all null until
    # the owner fills them in from Settings; the receipt renders the plain
    # (non-tax-invoice) layout until tax_id is set, even if VAT is enabled.
    tax_id: Mapped[str | None] = mapped_column(String(13))
    address: Mapped[str | None] = mapped_column(String(500))
    # Branch number for the tax invoice header — "00000" means สำนักงานใหญ่
    # (head office); left null for a single-branch shop that hasn't set one.
    branch_code: Mapped[str | None] = mapped_column(String(10))
    receipt_footer: Mapped[str | None] = mapped_column(String(500))
    # VAT settings — off by default so existing tenants see zero change in
    # totals until the owner explicitly opts in from Settings. vat_enabled is
    # kept separate from vat_rate so VAT can be toggled off without losing
    # the configured rate.
    vat_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    vat_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("7.00"), server_default="7.00")
    price_includes_tax: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # Loyalty points — off by default, same rationale as VAT above. A
    # customer earns floor(amount_paid / baht_per_point) points per sale and
    # each point is worth point_value_baht when redeemed as a checkout
    # discount (see sales_service.create_sale).
    loyalty_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    baht_per_point: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("25.00"), server_default="25.00")
    point_value_baht: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("1.00"), server_default="1.00")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
