import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.sale import PaymentMethod, SaleStatus
from app.schemas.promotion import AppliedPromotionResult


class SaleItemRequest(BaseModel):
    product_id: uuid.UUID
    qty: int = Field(gt=0)
    discount: Decimal = Field(default=Decimal(0), ge=0)


class SaleCreateRequest(BaseModel):
    # Generated client-side so a retried/duplicate submission (flaky network,
    # double-tap) never creates two sales for one checkout.
    client_uuid: uuid.UUID
    items: list[SaleItemRequest]
    discount: Decimal = Field(default=Decimal(0), ge=0)
    payment_method: PaymentMethod = PaymentMethod.cash
    customer_id: uuid.UUID | None = None
    redeem_points: int = Field(default=0, ge=0)
    coupon_code: str | None = Field(default=None, max_length=32)
    # When set (an offline-queued sale being replayed), promotions are
    # evaluated as of this moment rather than "now" — a sale rung up at
    # 23:50 during a promo that ended at midnight must not replay under the
    # rules that happened to be running when the network came back. Clamped
    # server-side to <= now and no older than 7 days (see sales_service).
    occurred_at: datetime | None = None


class SaleItemResult(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    name: str
    price: Decimal
    qty: int
    discount: Decimal
    promo_discount: Decimal
    promotion_name: str | None
    is_free_gift: bool
    line_total: Decimal
    refunded_qty: int


class RefundSummary(BaseModel):
    id: uuid.UUID
    refund_amount: Decimal
    refund_tax: Decimal
    reason: str | None
    created_at: datetime


class SaleResult(BaseModel):
    id: uuid.UUID
    receipt_no: str
    customer_id: uuid.UUID | None
    subtotal: Decimal
    discount: Decimal
    tax: Decimal
    price_includes_tax: bool
    total: Decimal
    points_earned: int
    points_redeemed: int
    points_discount: Decimal
    payment_method: PaymentMethod
    status: SaleStatus
    refunded_total: Decimal
    created_at: datetime
    items: list[SaleItemResult]
    refunds: list[RefundSummary] = Field(default_factory=list)
    applied_promotions: list[AppliedPromotionResult] = Field(default_factory=list)
    # Non-fatal notices — e.g. a line item oversold its stock. The sale still
    # goes through; this is what the register beeps about.
    warnings: list[str] = Field(default_factory=list)


class SalePreviewRequest(BaseModel):
    items: list[SaleItemRequest]
    discount: Decimal = Field(default=Decimal(0), ge=0)
    customer_id: uuid.UUID | None = None
    redeem_points: int = Field(default=0, ge=0)
    coupon_code: str | None = Field(default=None, max_length=32)


class PreviewLineResult(BaseModel):
    product_id: uuid.UUID
    name: str
    price: Decimal
    qty: int
    discount: Decimal
    promo_discount: Decimal
    promotion_name: str | None
    is_free_gift: bool
    line_total: Decimal


class SalePreviewResult(BaseModel):
    subtotal: Decimal
    discount: Decimal
    tax: Decimal
    total: Decimal
    price_includes_tax: bool
    points_earned: int
    points_redeemed: int
    points_discount: Decimal
    items: list[PreviewLineResult]
    applied_promotions: list[AppliedPromotionResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SaleListItem(BaseModel):
    id: uuid.UUID
    receipt_no: str
    total: Decimal
    payment_method: PaymentMethod
    status: SaleStatus
    created_at: datetime

    model_config = {"from_attributes": True}
