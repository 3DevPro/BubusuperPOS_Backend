import uuid

from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    business_name: str = Field(min_length=1, max_length=255)
    owner_name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class PinLoginRequest(BaseModel):
    tenant_id: uuid.UUID
    pin: str = Field(min_length=4, max_length=6)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class MeResponse(BaseModel):
    id: uuid.UUID
    # Exactly one of these is set — tenant_id for an ordinary shop account,
    # branch_id for a branch_champion (see UserRole.branch_champion).
    tenant_id: uuid.UUID | None
    branch_id: uuid.UUID | None
    name: str
    role: str
