import uuid
from datetime import datetime, time
from typing import Any

from pydantic import BaseModel, Field


class NotificationResponse(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    body: str
    payload: dict[str, Any]
    created_at: datetime
    read_at: datetime | None

    model_config = {"from_attributes": True}


class NotificationSettingsResponse(BaseModel):
    low_stock_enabled: bool
    low_stock_time: time
    low_stock_repeat_days: int
    daily_summary_enabled: bool
    daily_summary_time: time
    quiet_hours_start: time | None
    quiet_hours_end: time | None
    line_enabled: bool

    model_config = {"from_attributes": True}


class NotificationSettingsUpdateRequest(BaseModel):
    # Every field optional and only applied when explicitly sent — same
    # exclude_unset rationale as TenantSettingsUpdateRequest.
    low_stock_enabled: bool | None = None
    low_stock_time: time | None = None
    low_stock_repeat_days: int | None = Field(default=None, ge=1, le=90)
    daily_summary_enabled: bool | None = None
    daily_summary_time: time | None = None
    # Accept None explicitly to allow clearing a previously-set quiet-hours window.
    quiet_hours_start: time | None = Field(default=None)
    quiet_hours_end: time | None = Field(default=None)
    line_enabled: bool | None = None


class LineLinkTokenResponse(BaseModel):
    token: str
    oa_add_friend_url: str
    expires_at: datetime


class LineRecipientResponse(BaseModel):
    id: uuid.UUID
    display_name: str | None
    is_active: bool
    linked_at: datetime

    model_config = {"from_attributes": True}
