import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db import Base
from app.models.base import TenantScopedMixin


class InsuranceProductKind(str, enum.Enum):
    daily_income = "daily_income"
    accident = "accident"
    health = "health"
    property = "property"


class InsuranceProduct(Base):
    """Global catalog, not tenant-scoped — the same 4 products (see the
    case's Product Ladder) are offered to every tenant. A mock insurer
    catalog for this prototype: real underwriting and compliance under the
    OIC (คปภ.) framework is out of scope, see app/services/turbo/insurance_service.py."""

    __tablename__ = "turbo_insurance_products"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[InsuranceProductKind] = mapped_column()
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(String(500))
    # Only meaningful for non-daily_income products — daily_income is priced
    # dynamically from the tenant's own income profile instead (see
    # insurance_service.quote), so this is ignored for that kind.
    flat_monthly_premium: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class InsurancePolicyStatus(str, enum.Enum):
    active = "active"
    cancelled = "cancelled"
    expired = "expired"


class InsurancePolicy(TenantScopedMixin, Base):
    __tablename__ = "turbo_insurance_policies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("turbo_insurance_products.id"))
    # Only nonzero for daily_income policies — the amount paid out per zero-
    # revenue claim day (see InsuranceClaim).
    daily_benefit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    premium_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    premium_cycle: Mapped[str] = mapped_column(String(16), default="monthly", server_default="monthly")
    status: Mapped[InsurancePolicyStatus] = mapped_column(default=InsurancePolicyStatus.active)
    # Snapshot of the income profile the quote was underwritten on — same
    # snapshot-at-write-time rationale as Sale.tax_rate_snapshot, so a later
    # change in the tenant's sales pattern never rewrites what this policy
    # was actually sold on.
    income_profile_snapshot: Mapped[dict] = mapped_column(JSONB)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InsuranceClaimStatus(str, enum.Enum):
    # This prototype auto-approves parametric claims at creation time (the
    # evidence — a DailyClose with reason sick/accident — is re-verified
    # server-side before the row is even written, see claim_service), so
    # "pending" never actually appears yet; kept for when a real insurer
    # integration needs a manual-review step.
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class InsuranceClaim(TenantScopedMixin, Base):
    __tablename__ = "turbo_insurance_claims"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    policy_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("turbo_insurance_policies.id"))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    days: Mapped[int] = mapped_column(Integer)
    benefit_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    # {"reasons": {"2026-08-09": "sick", ...}} — the DailyClose evidence the
    # claim was approved on, so an approved claim stays explainable even if
    # the underlying DailyClose rows later change.
    evidence: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[InsuranceClaimStatus] = mapped_column(default=InsuranceClaimStatus.approved)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
