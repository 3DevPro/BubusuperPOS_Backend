import uuid

from pydantic import BaseModel, Field


class CategoryCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class CategoryUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class CategoryResponse(BaseModel):
    id: uuid.UUID
    name: str

    model_config = {"from_attributes": True}
