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


class LoanApplicationCreateRequest(BaseModel):
    product_code: str = Field(min_length=1, max_length=64)
    requested_amount: Decimal = Field(gt=0)
    collateral_value: Decimal = Field(gt=0)
    term_months: int = Field(gt=0)


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
