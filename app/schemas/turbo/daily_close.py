import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.turbo.daily_close import DailyCloseReason


class DailyCloseCreateRequest(BaseModel):
    business_date: date
    closed_reason: DailyCloseReason = DailyCloseReason.open
    extra_expense: Decimal = Field(default=Decimal("0"), ge=0)
    note: str | None = Field(default=None, max_length=500)


class DailyCloseResponse(BaseModel):
    id: uuid.UUID
    business_date: date
    closed_reason: DailyCloseReason
    extra_expense: Decimal
    note: str | None
    closed_at: datetime

    model_config = {"from_attributes": True}
