import uuid

from pydantic import BaseModel, Field

from app.models.user import UserRole


class StaffCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    role: UserRole = UserRole.cashier
    pin: str | None = Field(default=None, min_length=4, max_length=6)


class StaffResponse(BaseModel):
    id: uuid.UUID
    name: str
    role: UserRole
    is_active: bool

    model_config = {"from_attributes": True}


class StaffUpdateRequest(BaseModel):
    role: UserRole | None = None
    is_active: bool | None = None
    pin: str | None = Field(default=None, min_length=4, max_length=6)
