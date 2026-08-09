import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.purchase_order import PurchaseOrderStatus


class PurchaseOrderItemRequest(BaseModel):
    product_id: uuid.UUID
    qty: int = Field(gt=0)
    unit_cost: Decimal = Field(ge=0)


class PurchaseOrderCreateRequest(BaseModel):
    supplier_id: uuid.UUID
    items: list[PurchaseOrderItemRequest]
    notes: str | None = Field(default=None, max_length=500)


class PurchaseOrderReceiveItemRequest(BaseModel):
    purchase_order_item_id: uuid.UUID
    qty: int = Field(gt=0)


class PurchaseOrderReceiveRequest(BaseModel):
    items: list[PurchaseOrderReceiveItemRequest]


class PurchaseOrderItemResult(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    name: str
    qty_ordered: int
    qty_received: int
    unit_cost: Decimal


class PurchaseOrderResult(BaseModel):
    id: uuid.UUID
    order_no: str
    supplier_id: uuid.UUID
    status: PurchaseOrderStatus
    notes: str | None
    created_at: datetime
    items: list[PurchaseOrderItemResult]


class PurchaseOrderListItem(BaseModel):
    id: uuid.UUID
    order_no: str
    supplier_id: uuid.UUID
    status: PurchaseOrderStatus
    created_at: datetime

    model_config = {"from_attributes": True}
