import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class ProductCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    sku: str | None = Field(default=None, max_length=64)
    barcode: str | None = Field(default=None, max_length=64)
    category_id: uuid.UUID | None = None
    image_url: str | None = Field(default=None, max_length=1024)
    sell_price: Decimal = Field(ge=0)
    cost_price: Decimal = Field(default=Decimal(0), ge=0)
    track_stock: bool = True
    stock_qty: int = Field(default=0, ge=0)
    low_stock_threshold: int = Field(default=5, ge=0)
    expiry_date: date | None = None


class ProductUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    sku: str | None = Field(default=None, max_length=64)
    barcode: str | None = Field(default=None, max_length=64)
    category_id: uuid.UUID | None = None
    image_url: str | None = Field(default=None, max_length=1024)
    sell_price: Decimal | None = Field(default=None, ge=0)
    cost_price: Decimal | None = Field(default=None, ge=0)
    track_stock: bool | None = None
    low_stock_threshold: int | None = Field(default=None, ge=0)
    expiry_date: date | None = None
    # stock_qty is deliberately not editable here — go through
    # POST /inventory/adjust so every change leaves a stock_movements trail.


class ProductImageUploadResponse(BaseModel):
    image_url: str


class ProductLookupResponse(BaseModel):
    found: bool
    name: str | None = None
    image_url: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    brand: str | None = None
    source: str | None = None
    cached: bool = False


class ProductResponse(BaseModel):
    id: uuid.UUID
    sku: str | None
    barcode: str | None
    name: str
    category_id: uuid.UUID | None
    image_url: str | None
    sell_price: Decimal
    cost_price: Decimal
    track_stock: bool
    stock_qty: int
    low_stock_threshold: int
    expiry_date: date | None
    is_active: bool

    model_config = {"from_attributes": True}
