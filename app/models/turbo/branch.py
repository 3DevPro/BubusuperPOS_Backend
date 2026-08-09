import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db import Base


class Branch(Base):
    """A physical Ngernturbo branch — not tenant-scoped. This is the unit a
    Branch Champion (see UserRole.branch_champion) works out of; prospects
    and leads below belong to a branch, never to a shop tenant, since a
    prospect isn't a POS customer (yet)."""

    __tablename__ = "turbo_branches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    province: Mapped[str] = mapped_column(String(100))
    lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    lng: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MerchantProspectStatus(str, enum.Enum):
    not_visited = "not_visited"
    visited = "visited"
    converted = "converted"
    not_interested = "not_interested"


class MerchantProspect(Base):
    """A shop within a branch's walking radius that isn't a POS tenant —
    who the Champion's Morning Route (see the case's "500-Meter Playbook")
    visits. Deliberately has no link to Tenant: if a prospect does sign up
    later, that's an ordinary, unrelated Tenant row — this app doesn't
    assume or require prior contact before a shop can become a customer."""

    __tablename__ = "turbo_merchant_prospects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("turbo_branches.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    business_type: Mapped[str | None] = mapped_column(String(100))
    address: Mapped[str | None] = mapped_column(String(500))
    phone: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[MerchantProspectStatus] = mapped_column(default=MerchantProspectStatus.not_visited)
    note: Mapped[str | None] = mapped_column(String(500))
    last_visited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LeadSource(str, enum.Enum):
    o2o_web = "o2o_web"
    visit = "visit"
    referral = "referral"


class LeadStatus(str, enum.Enum):
    new = "new"
    contacted = "contacted"
    converted = "converted"
    lost = "lost"


class Lead(Base):
    """A contact worth following up on — from the public O2O quote form, a
    Champion's own visit, or a referral. `first_response_at` is what the
    SLA countdown in the branch app (target: 15 minutes) is measured
    against, matching the case's Engine 1B O2O slide."""

    __tablename__ = "turbo_leads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assigned_branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("turbo_branches.id"), index=True
    )
    prospect_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("turbo_merchant_prospects.id")
    )
    source: Mapped[LeadSource] = mapped_column()
    name: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    occupation: Mapped[str | None] = mapped_column(String(100))
    age: Mapped[int | None] = mapped_column()
    # Snapshot of what the O2O quote showed this lead — kept even if pricing
    # assumptions change later, same rationale as InsurancePolicy's snapshot.
    quoted_daily_benefit: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    quoted_premium: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    status: Mapped[LeadStatus] = mapped_column(default=LeadStatus.new)
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
