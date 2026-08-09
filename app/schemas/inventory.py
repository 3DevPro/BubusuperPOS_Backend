import uuid

from pydantic import BaseModel, Field, field_validator

from app.models.stock import StockMovementType


class StockAdjustRequest(BaseModel):
    product_id: uuid.UUID
    qty_delta: int = Field(description="positive to add stock, negative to remove")
    type: StockMovementType = StockMovementType.adjust
    note: str | None = Field(default=None, max_length=255)

    @field_validator("qty_delta")
    @classmethod
    def _not_zero(cls, v: int) -> int:
        if v == 0:
            raise ValueError("qty_delta must not be zero")
        return v

    @field_validator("type")
    @classmethod
    def _not_sale(cls, v: StockMovementType) -> StockMovementType:
        if v == StockMovementType.sale:
            raise ValueError("sale movements are created by checkout, not this endpoint")
        return v


class StockAdjustResult(BaseModel):
    product_id: uuid.UUID
    stock_qty: int


class LowStockItem(BaseModel):
    id: uuid.UUID
    name: str
    stock_qty: int
    low_stock_threshold: int

    model_config = {"from_attributes": True}
