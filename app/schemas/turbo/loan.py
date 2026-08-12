import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.turbo.loan import LoanCollateralKind


class LoanProductResponse(BaseModel):
    id: uuid.UUID
    code: str
    collateral_kind: LoanCollateralKind
    name: str
    description: str
    max_principal: Decimal
    monthly_interest_rate: Decimal
    min_term_months: int
    max_term_months: int

    model_config = {"from_attributes": True}


class LoanQuoteRequest(BaseModel):
    product_code: str = Field(min_length=1, max_length=64)
    requested_amount: Decimal = Field(gt=0)
    collateral_value: Decimal = Field(gt=0)
    term_months: int = Field(gt=0)


class LoanQuoteResponse(BaseModel):
    product_code: str
    approved_amount: Decimal
    term_months: int
    monthly_interest_rate: Decimal
    monthly_installment: Decimal
    total_interest: Decimal
    total_repayment: Decimal
    # Human-readable reasons approved_amount is less than requested_amount —
    # empty when the request was granted in full. See loan_service.quote.
    cap_reasons: list[str]


class LoanCollateralDetail(BaseModel):
    """Freeform checklist the tenant fills in at apply() time — shape is
    validated here at the edge, but the DB column (JSONB) doesn't enforce
    it, same rationale as income_profile_snapshot/cap_reasons. Field names
    stay generic across collateral kinds; the frontend just relabels them
    (e.g. land_title shows "เลขที่โฉนด" for registration_no)."""

    registration_no: str | None = Field(default=None, max_length=100)
    brand_model: str | None = Field(default=None, max_length=100)
    year: str | None = Field(default=None, max_length=10)
    note: str | None = Field(default=None, max_length=500)


class LoanApplicationCreateRequest(BaseModel):
    product_code: str = Field(min_length=1, max_length=64)
    requested_amount: Decimal = Field(gt=0)
    collateral_value: Decimal = Field(gt=0)
    term_months: int = Field(gt=0)
    collateral_detail: LoanCollateralDetail = LoanCollateralDetail()


class LoanApplicationResponse(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    requested_amount: Decimal
    collateral_value: Decimal
    term_months: int
    approved_amount: Decimal
    monthly_installment: Decimal
    monthly_interest_rate_snapshot: Decimal
    credit_tier_snapshot: str
    cap_reasons: list[str]
    status: str
    created_at: datetime
    decided_at: datetime | None

    model_config = {"from_attributes": True}


class LoanApplicationEventResponse(BaseModel):
    id: uuid.UUID
    from_status: str | None
    to_status: str
    actor_name: str
    actor_kind: str
    note: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class LoanApplicationDetailResponse(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    requested_amount: Decimal
    collateral_value: Decimal
    collateral_detail: dict
    term_months: int
    approved_amount: Decimal
    monthly_installment: Decimal
    monthly_interest_rate_snapshot: Decimal
    credit_tier_snapshot: str
    cap_reasons: list[str]
    status: str
    rejection_reason: str | None
    stage_started_at: datetime
    created_at: datetime
    decided_at: datetime | None
    # Seconds until the auto-advance clock moves this on by itself — null
    # once the application is past every review stage, or if the clock is
    # disabled (see app/core/turbo_config.LOAN_AUTO_ADVANCE_ENABLED).
    next_stage_eta_seconds: int | None
    # Only set when status == rejected — when the tenant may submit again.
    can_reapply_at: datetime | None
    events: list[LoanApplicationEventResponse]

    model_config = {"from_attributes": True}


class LoanEligibilityResponse(BaseModel):
    can_apply: bool
    reason: str | None
    cooldown_until: datetime | None
    in_flight_application_id: uuid.UUID | None


class LoanReviewAdvanceRequest(BaseModel):
    to_status: str
    note: str | None = Field(default=None, max_length=500)


class LoanRejectRequest(BaseModel):
    reason: str = Field(min_length=5, max_length=500)


class LoanReviewItemResponse(BaseModel):
    """What a Branch Champion sees in the review queue/detail — this is the
    first place a champion (BranchContext, no tenant_id — see
    app/core/branch_scope.py) sees any tenant data at all, so this response
    model is the actual access-control boundary: whatever field isn't listed
    here can't leak, regardless of what LoanApplication holds. Deliberately
    excludes income_profile_snapshot (raw daily sales history isn't needed
    to review a loan; credit_tier_snapshot is enough) and cap_reasons
    (explains loan pricing, not something a reviewer approves/rejects on)."""

    id: uuid.UUID
    tenant_name: str
    tenant_phone: str | None
    product_id: uuid.UUID
    approved_amount: Decimal
    monthly_installment: Decimal
    term_months: int
    collateral_kind: str
    collateral_value: Decimal
    collateral_detail: dict
    credit_tier_snapshot: str
    status: str
    stage_started_at: datetime
    created_at: datetime


class LoanReviewDetailResponse(LoanReviewItemResponse):
    events: list[LoanApplicationEventResponse]


class LoanAccountResponse(BaseModel):
    id: uuid.UUID
    application_id: uuid.UUID
    product_id: uuid.UUID
    account_number: str
    principal: Decimal
    monthly_interest_rate: Decimal
    term_months: int
    monthly_installment: Decimal
    status: str
    disbursed_at: datetime
    first_due_date: date

    model_config = {"from_attributes": True}


class LoanInstallmentResponse(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID
    sequence: int
    due_date: date
    principal_component: Decimal
    interest_component: Decimal
    amount_due: Decimal
    status: str
    paid_at: datetime | None
    paid_amount: Decimal | None
    paid_reference: str | None
    # Derived at read time from due_date, not a stored column — see
    # app/models/turbo/loan.py LoanInstallment docstring.
    is_overdue: bool
    days_overdue: int | None

    model_config = {"from_attributes": True}


class LoanAccountSummaryResponse(BaseModel):
    account: LoanAccountResponse
    # Sum of remaining amount_due across unpaid installments — principal
    # *and* interest still owed, not the pure principal balance.
    outstanding_balance: Decimal
    installments_total: int
    installments_paid: int
    on_time_payments: int
    next_due_date: date | None
    next_due_amount: Decimal | None
    due_in_days: int | None
    has_overdue: bool
    overdue_count: int
    overdue_amount: Decimal
    max_days_overdue: int | None


class LoanPaymentRequest(BaseModel):
    # This prototype's "pay" is the payer confirming they sent the money via
    # the shown QR — no real settlement feed is checked against it, see
    # loan_service.pay_installment.
    amount: Decimal = Field(gt=0)
    reference: str | None = Field(default=None, max_length=255)


class CreditStandingResponse(BaseModel):
    credit_tier: str
    credit_limit: Decimal
    streak_days: int
    on_time_payments: int
    next_tier_in_days: int | None
    next_tier_requirement: str | None
