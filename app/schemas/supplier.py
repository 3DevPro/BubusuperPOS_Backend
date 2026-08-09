import uuid

from pydantic import BaseModel, Field


class SupplierCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=255)
    address: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=500)


class SupplierUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=255)
    address: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=500)


class SupplierResponse(BaseModel):
    id: uuid.UUID
    name: str
    phone: str | None
    email: str | None
    address: str | None
    notes: str | None

    model_config = {"from_attributes": True}
