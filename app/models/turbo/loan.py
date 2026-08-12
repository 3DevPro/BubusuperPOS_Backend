import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db import Base
from app.models.base import TenantScopedMixin


class LoanCollateralKind(str, enum.Enum):
    motorcycle = "motorcycle"
    car = "car"
    tractor = "tractor"
    land_title = "land_title"


class LoanProduct(Base):
    """Global catalog, not tenant-scoped — the same 4 secured-loan products
    (the case's Product Ladder: motorcycle/car/tractor/land title) are
    offered to every tenant, same pattern as InsuranceProduct. A mock lender
    catalog for this prototype: real underwriting (e-KYC, credit bureau,
    collateral appraisal) is out of scope, see app/services/turbo/loan_service.py."""

    __tablename__ = "turbo_loan_products"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    collateral_kind: Mapped[LoanCollateralKind] = mapped_column()
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(String(500))
    max_principal: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    monthly_interest_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4))
    min_term_months: Mapped[int] = mapped_column(Integer, default=6, server_default="6")
    max_term_months: Mapped[int] = mapped_column(Integer, default=36, server_default="36")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class LoanApplicationStatus(str, enum.Enum):
    submitted = "submitted"
    doc_review = "doc_review"
    collateral_check = "collateral_check"
    under_review = "under_review"
    approved = "approved"
    disbursed = "disbursed"
    rejected = "rejected"


class LoanApplication(TenantScopedMixin, Base):
    """One application per attempt to borrow — `apply()` re-derives the quote
    server-side and snapshots it here (income_profile_snapshot,
    credit_tier_snapshot) so what the tenant was actually offered stays
    explainable even if their sales pattern changes afterward, same
    snapshot-at-write-time rationale as InsurancePolicy.income_profile_snapshot."""

    __tablename__ = "turbo_loan_applications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("turbo_loan_products.id"))
    requested_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    collateral_value: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    term_months: Mapped[int] = mapped_column(Integer)
    approved_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    monthly_installment: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    monthly_interest_rate_snapshot: Mapped[Decimal] = mapped_column(Numeric(6, 4))
    income_profile_snapshot: Mapped[dict] = mapped_column(JSONB)
    credit_tier_snapshot: Mapped[str] = mapped_column(String(16))
    # {"reasons": ["จำกัดที่ ฿10,000 ตามระดับเครดิตปัจจุบัน", ...]} — why
    # approved_amount is less than requested_amount, if it is. Kept even
    # after disbursal so the application stays explainable later.
    cap_reasons: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    assigned_branch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("turbo_branches.id"), index=True
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("turbo_leads.id"))
    status: Mapped[LoanApplicationStatus] = mapped_column(default=LoanApplicationStatus.submitted)
    # Freeform checklist the tenant fills in at apply() time — shape is
    # validated at the Pydantic edge (LoanCollateralDetail), not the DB, same
    # rationale as income_profile_snapshot/cap_reasons above.
    collateral_detail: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    # Required only at the reject() transition (Field(min_length=5) on the
    # request schema) — not NOT NULL here, matching every other
    # "required-for-this-transition-only" field already in this project.
    rejection_reason: Mapped[str | None] = mapped_column(String(500))
    # Reset on every status change — the review-stage auto-advance clock (see
    # loan_service._auto_advance) measures elapsed time from this, not from
    # created_at, so it reflects time-in-*current*-stage.
    stage_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Now also set on approved/rejected, not just disbursed — apply()'s
    # reject-cooldown check reads this.
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoanApplicationEvent(Base):
    """Append-only status-change ledger — deliberately *not* TenantScopedMixin
    because a row's writer can be either a tenant owner/manager (TenantContext)
    or a branch champion (BranchContext, which has no tenant_id at all — see
    app/core/branch_scope.py). tenant_id is denormalized here so a tenant can
    read their own application's timeline directly. AuditLog can't be reused
    for this: it has no entity_id/entity_type to filter by (see
    app/models/audit_log.py), only a free-text summary."""

    __tablename__ = "turbo_loan_application_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("turbo_loan_applications.id"), index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), index=True)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("turbo_branches.id"))
    from_status: Mapped[str | None] = mapped_column(String(32))
    # String, not the LoanApplicationStatus PG enum — an append-only ledger
    # shouldn't need a migration every time the review pipeline grows a
    # stage, and it sidesteps "can't reference an enum value added earlier in
    # the same transaction" entirely (see the migration's own comment).
    to_status: Mapped[str] = mapped_column(String(32))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    actor_name: Mapped[str] = mapped_column(String(255))
    actor_kind: Mapped[str] = mapped_column(String(16))  # 'merchant' | 'champion' | 'system'
    note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LoanAccountStatus(str, enum.Enum):
    active = "active"
    closed = "closed"


class LoanAccount(TenantScopedMixin, Base):
    """Created once an application is disbursed — `disburse()` auto-approves
    at prototype level (no real e-KYC/credit-bureau check) and immediately
    writes every LoanInstallment row up front, so there's never a scheduler
    generating them lazily."""

    __tablename__ = "turbo_loan_accounts"
    __table_args__ = (
        # One account per application — see migration
        # c1a9f6d2e7b3_add_turbo_loan_account_uniqueness for the rationale
        # (backstop for the disburse() double-disbursement race).
        UniqueConstraint("application_id", name="uq_turbo_loan_accounts_application_id"),
        Index(
            "uq_turbo_loan_accounts_tenant_active",
            "tenant_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("turbo_loan_applications.id"))
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("turbo_loan_products.id"))
    account_number: Mapped[str] = mapped_column(String(32), unique=True)
    principal: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    monthly_interest_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4))
    term_months: Mapped[int] = mapped_column(Integer)
    monthly_installment: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    status: Mapped[LoanAccountStatus] = mapped_column(default=LoanAccountStatus.active)
    disbursed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    first_due_date: Mapped[date] = mapped_column(Date)


class LoanInstallmentStatus(str, enum.Enum):
    unpaid = "unpaid"
    paid = "paid"


class LoanInstallment(TenantScopedMixin, Base):
    """One row per scheduled payment, written in full at disbursal time.
    Deliberately has no "overdue" status: whether an unpaid installment is
    overdue is derived from due_date at read time (see loan_service), which
    means this table never needs a cron/scheduler to stay correct."""

    __tablename__ = "turbo_loan_installments"
    __table_args__ = (UniqueConstraint("account_id", "sequence", name="uq_turbo_loan_installments_account_seq"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("turbo_loan_accounts.id"))
    sequence: Mapped[int] = mapped_column(Integer)
    due_date: Mapped[date] = mapped_column(Date)
    principal_component: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    interest_component: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    amount_due: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    status: Mapped[LoanInstallmentStatus] = mapped_column(default=LoanInstallmentStatus.unpaid)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # Freeform reference the payer types in (e.g. a bank transfer ref) — this
    # prototype's QR payment records what the payer says they paid, it does
    # not verify against any real settlement feed (see loan_service.pay_installment).
    paid_reference: Mapped[str | None] = mapped_column(String(255))
