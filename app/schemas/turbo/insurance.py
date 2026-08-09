import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.turbo.insurance import InsuranceProductKind


class InsuranceProductResponse(BaseModel):
    id: uuid.UUID
    code: str
    kind: InsuranceProductKind
    name: str
    description: str
    flat_monthly_premium: Decimal

    model_config = {"from_attributes": True}


class InsuranceQuoteResponse(BaseModel):
    product_code: str
    daily_benefit: Decimal
    premium_amount: Decimal
    premium_cycle: str


class InsurancePurchaseRequest(BaseModel):
    product_code: str = Field(min_length=1, max_length=64)


class InsurancePolicyResponse(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    daily_benefit: Decimal
    premium_amount: Decimal
    premium_cycle: str
    status: str
    starts_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


class DetectedClaimResponse(BaseModel):
    policy_id: uuid.UUID
    start_date: date
    end_date: date
    days: int
    benefit_amount: Decimal
    reasons: dict[str, str]


class InsuranceClaimCreateRequest(BaseModel):
    policy_id: uuid.UUID
    start_date: date
    end_date: date


class InsuranceClaimResponse(BaseModel):
    id: uuid.UUID
    policy_id: uuid.UUID
    start_date: date
    end_date: date
    days: int
    benefit_amount: Decimal
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}
