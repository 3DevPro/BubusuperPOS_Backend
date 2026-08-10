import uuid
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.turbo.loan import LoanCollateralKind


class PublicQuoteRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=32)
    occupation: str = Field(min_length=1, max_length=100)
    age: int = Field(ge=15, le=100)
    monthly_budget: Decimal = Field(gt=0)
    # Used to route the resulting Lead to a same-province branch — the
    # nearest real distance would need geolocation the web form doesn't
    # collect, so province is the practical stand-in for "nearest".
    province: str | None = Field(default=None, max_length=100)


class PublicQuoteResponse(BaseModel):
    daily_benefit: Decimal
    premium_amount: Decimal
    premium_cycle: str = "daily"
    lead_id: uuid.UUID


class PublicLoanQuoteRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=32)
    occupation: str = Field(min_length=1, max_length=100)
    age: int = Field(ge=15, le=100)
    collateral_kind: LoanCollateralKind
    collateral_value: Decimal = Field(gt=0)
    requested_amount: Decimal = Field(gt=0)
    term_months: int = Field(gt=0)
    province: str | None = Field(default=None, max_length=100)


class PublicLoanQuoteResponse(BaseModel):
    approved_amount: Decimal
    term_months: int
    monthly_interest_rate: Decimal
    monthly_installment: Decimal
    total_interest: Decimal
    total_repayment: Decimal
    lead_id: uuid.UUID


class LoanTermBoundsResponse(BaseModel):
    collateral_kind: str
    min_term_months: int
    max_term_months: int
