import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, EmailStr, Field

from app.models.turbo.branch import (
    LeadSource,
    LeadStatus,
    MerchantProspectStatus,
    ProspectApplicationInterest,
    ProspectContactStatus,
)


class BranchSignupRequest(BaseModel):
    # If branch_code already exists, the new staff account joins that branch
    # instead of creating a duplicate — see branch_service.signup.
    branch_code: str = Field(min_length=1, max_length=32)
    branch_name: str = Field(min_length=1, max_length=255)
    province: str = Field(min_length=1, max_length=100)
    staff_name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class BranchResponse(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    province: str

    model_config = {"from_attributes": True}


class ProspectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    business_type: str | None = Field(default=None, max_length=100)
    address: str | None = Field(default=None, max_length=500)
    phone: str | None = Field(default=None, max_length=32)
    application_interest: ProspectApplicationInterest = ProspectApplicationInterest.not_applied
    # No contact_status here on purpose — a prospect always starts
    # not_scheduled and can only move to called/met through
    # update_prospect_contact_status, so the leaderboard's called_at/met_at
    # timestamps always reflect a real, individually-timestamped action
    # rather than something backdated at creation time.


class ProspectVisitRequest(BaseModel):
    status: MerchantProspectStatus
    note: str | None = Field(default=None, max_length=500)


class ProspectContactStatusUpdateRequest(BaseModel):
    contact_status: ProspectContactStatus


class ProspectApplicationInterestUpdateRequest(BaseModel):
    application_interest: ProspectApplicationInterest


class ProspectResponse(BaseModel):
    id: uuid.UUID
    name: str
    business_type: str | None
    address: str | None
    phone: str | None
    status: MerchantProspectStatus
    application_interest: ProspectApplicationInterest
    contact_status: ProspectContactStatus
    contact_status_updated_at: datetime | None
    called_at: datetime | None
    met_at: datetime | None
    note: str | None
    last_visited_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class LeadRespondRequest(BaseModel):
    status: LeadStatus


class LeadResponse(BaseModel):
    id: uuid.UUID
    prospect_id: uuid.UUID | None
    source: LeadSource
    name: str
    phone: str | None
    occupation: str | None
    age: int | None
    quoted_daily_benefit: Decimal | None
    quoted_premium: Decimal | None
    quoted_loan_amount: Decimal | None
    quoted_monthly_installment: Decimal | None
    collateral_kind: str | None
    status: LeadStatus
    first_response_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class LeaderboardEntryResponse(BaseModel):
    branch_id: uuid.UUID
    branch_name: str
    prospects_visited: int
    prospects_contacted: int
    leads_contacted: int
    score: int
