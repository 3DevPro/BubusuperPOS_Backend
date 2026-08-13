import enum
import uuid
from datetime import date, datetime, time

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Time, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db import Base
from app.models.base import TenantScopedMixin


class NotificationKind(str, enum.Enum):
    low_stock = "low_stock"
    daily_summary = "daily_summary"
    system = "system"


class NotificationChannelName(str, enum.Enum):
    inapp = "inapp"
    line = "line"


class DeliveryStatus(str, enum.Enum):
    pending = "pending"
    sent = "sent"
    failed = "failed"
    skipped = "skipped"


class Notification(TenantScopedMixin, Base):
    """The inbox row itself — always created regardless of channel
    availability, so "no LINE account linked" never means "the owner never
    finds out". `dedupe_key` plus the unique constraint below is the entire
    idempotency mechanism for the scheduler sweep: inserting the same key
    twice is a silent no-op (see notification_service.create), which is what
    makes a 15-minute poll-and-check sweep safe to run forever."""

    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("tenant_id", "dedupe_key", name="uq_notifications_tenant_dedupe"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Null means "the whole tenant" (every role) rather than one specific
    # user — both low_stock and daily_summary notifications are shop-wide.
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    kind: Mapped[NotificationKind] = mapped_column()
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(String(2000))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    dedupe_key: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationSettings(TenantScopedMixin, Base):
    """One row per tenant. All times are interpreted in the tenant's own
    timezone (Tenant.timezone) by the scheduler jobs, same as every other
    "today"/"business day" calculation in report_service."""

    __tablename__ = "notification_settings"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_notification_settings_tenant"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    low_stock_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    low_stock_time: Mapped[time] = mapped_column(Time, default=time(9, 0), server_default="09:00:00")
    # A product that's been low for this many days re-alerts even if it
    # never restocked in between — otherwise the very first digest would be
    # the only warning a shop owner ever sees for a chronically low item.
    low_stock_repeat_days: Mapped[int] = mapped_column(Integer, default=7, server_default="7")
    daily_summary_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    daily_summary_time: Mapped[time] = mapped_column(Time, default=time(20, 0), server_default="20:00:00")
    quiet_hours_start: Mapped[time | None] = mapped_column(Time)
    quiet_hours_end: Mapped[time | None] = mapped_column(Time)
    line_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class NotificationDelivery(TenantScopedMixin, Base):
    """One row per (notification, channel, recipient) — a notification with
    two linked LINE accounts fans out to two delivery rows here plus one
    inapp row. The unique constraint makes fan-out idempotent the same way
    Notification.dedupe_key makes notification creation idempotent."""

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "notification_id", "channel", "recipient", name="uq_notification_deliveries_channel_recipient"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    notification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notifications.id"), index=True
    )
    channel: Mapped[NotificationChannelName] = mapped_column()
    # Null for the inapp channel (the notification row itself is the
    # delivery); a LINE user id for the line channel.
    recipient: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[DeliveryStatus] = mapped_column(default=DeliveryStatus.pending)
    # Quiet-hours deferral is applied here, not by delaying the notification
    # row itself — the inbox always shows the alert immediately, only the
    # push is held back until the window opens.
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(500))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LineLinkToken(TenantScopedMixin, Base):
    """Short-lived code an owner sends as a LINE chat message to link their
    OA-added LINE account to this tenant. `token` is the primary key
    (looked up by the webhook, which has no TenantContext of its own to
    scope through) rather than a UUID, since it's also the 6-character code
    shown on screen — a random UUID would be unusable to type/read aloud."""

    __tablename__ = "line_link_tokens"

    token: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LineRecipient(TenantScopedMixin, Base):
    """A LINE account linked to this tenant via LineLinkToken — one LINE
    account can only ever link to one shop (line_user_id is globally
    unique), which is the natural constraint for a shop owner's personal
    LINE account."""

    __tablename__ = "line_recipients"
    __table_args__ = (UniqueConstraint("line_user_id", name="uq_line_recipients_line_user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    line_user_id: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    display_name: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LowStockAlertState(TenantScopedMixin, Base):
    """Tracks the last time each product was included in a low-stock
    digest, so the sweep only re-alerts on a product that's newly low or has
    been low for low_stock_repeat_days — never every 15 minutes forever.
    Deleted outright as soon as a product's stock rises back above its
    threshold, so a restock-then-drop-again re-alerts immediately instead of
    waiting out the repeat window (see low_stock_job.sweep)."""

    __tablename__ = "low_stock_alert_state"
    __table_args__ = (
        UniqueConstraint("tenant_id", "product_id", name="uq_low_stock_alert_state_tenant_product"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"))
    last_alerted_on: Mapped[date] = mapped_column(Date)
    last_alerted_qty: Mapped[int] = mapped_column(Integer)
