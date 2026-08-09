import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.sale import SaleStatus


class RefundItemRequest(BaseModel):
    sale_item_id: uuid.UUID
    qty: int = Field(gt=0)


class RefundCreateRequest(BaseModel):
    # Generated client-side so a retried/duplicate submission never creates
    # two refunds for one return, same rationale as Sale.client_uuid.
    client_uuid: uuid.UUID
    # None/omitted = refund every unit still remaining on the sale (full refund).
    items: list[RefundItemRequest] | None = None
    reason: str | None = Field(default=None, max_length=255)


class RefundItemResult(BaseModel):
    sale_item_id: uuid.UUID
    qty: int
    amount: Decimal


class RefundResult(BaseModel):
    id: uuid.UUID
    sale_id: uuid.UUID
    refund_amount: Decimal
    refund_tax: Decimal
    reason: str | None
    created_at: datetime
    items: list[RefundItemResult]
    sale_status: SaleStatus
