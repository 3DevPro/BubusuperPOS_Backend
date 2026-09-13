import uuid
from datetime import datetime, time
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.promotion import PromotionKind, PromotionScope


class PromotionCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    kind: PromotionKind
    scope: PromotionScope = PromotionScope.product
    percent_off: Decimal | None = Field(default=None, gt=0, le=100)
    amount_off: Decimal | None = Field(default=None, gt=0)
    buy_qty: int | None = Field(default=None, gt=0)
    get_qty: int | None = Field(default=None, gt=0)
    bundle_price: Decimal | None = Field(default=None, ge=0)
    coupon_code: str | None = Field(default=None, max_length=32)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    active_weekdays: str | None = Field(default=None, min_length=7, max_length=7)
    daily_start_time: time | None = None
    daily_end_time: time | None = None
    max_uses: int | None = Field(default=None, gt=0)
    priority: int = 100
    stackable: bool = False
    target_product_ids: list[uuid.UUID] = Field(default_factory=list)
    target_category_ids: list[uuid.UUID] = Field(default_factory=list)


class PromotionUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    scope: PromotionScope | None = None
    percent_off: Decimal | None = Field(default=None, gt=0, le=100)
    amount_off: Decimal | None = Field(default=None, gt=0)
    buy_qty: int | None = Field(default=None, gt=0)
    get_qty: int | None = Field(default=None, gt=0)
    bundle_price: Decimal | None = Field(default=None, ge=0)
    coupon_code: str | None = Field(default=None, max_length=32)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    active_weekdays: str | None = Field(default=None, min_length=7, max_length=7)
    daily_start_time: time | None = None
    daily_end_time: time | None = None
    max_uses: int | None = None
    priority: int | None = None
    stackable: bool | None = None
    is_active: bool | None = None
    # Present (even as an empty list) means "replace the target list" —
    # omitted means "leave targets unchanged". Distinguished via
    # exclude_unset, same as every other partial-update schema in this repo.
    target_product_ids: list[uuid.UUID] | None = None
    target_category_ids: list[uuid.UUID] | None = None


class PromotionResponse(BaseModel):
    id: uuid.UUID
    name: str
    kind: PromotionKind
    scope: PromotionScope
    percent_off: Decimal | None
    amount_off: Decimal | None
    buy_qty: int | None
    get_qty: int | None
    bundle_price: Decimal | None
    coupon_code: str | None
    starts_at: datetime | None
    ends_at: datetime | None
    active_weekdays: str | None
    daily_start_time: time | None
    daily_end_time: time | None
    max_uses: int | None
    uses_count: int
    priority: int
    stackable: bool
    is_active: bool
    target_product_ids: list[uuid.UUID]
    target_category_ids: list[uuid.UUID]

    model_config = {"from_attributes": True}


class AppliedPromotionResult(BaseModel):
    promotion_id: uuid.UUID
    name: str
    discount_amount: Decimal
