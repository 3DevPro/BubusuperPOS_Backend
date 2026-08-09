import uuid

from pydantic import BaseModel, Field


class CustomerCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=20)


class CustomerUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=20)


class CustomerResponse(BaseModel):
    id: uuid.UUID
    name: str
    phone: str | None
    points_balance: int

    model_config = {"from_attributes": True}
