"""Pluggable delivery channels for notifications — mirrors the
ProductLookupProvider Protocol pattern in app/ai/product_lookup_provider.py.
Adding LineChannel here needed no changes to notification_service.py or
notification_dispatch.py — that's the point of the Protocol."""

from typing import Protocol

from app.core.tenancy import TenantContext
from app.models.notification import (
    LineRecipient,
    Notification,
    NotificationChannelName,
    NotificationDelivery,
    NotificationSettings,
)
from app.services import line_service


class NotificationChannel(Protocol):
    name: NotificationChannelName
    # False for channels where the notification row itself is sufficient
    # (in-app) — True for anything that makes an outbound call and should
    # be deferred by quiet hours / retried on failure (LINE).
    requires_dispatch: bool

    async def recipients(self, ctx: TenantContext, settings: NotificationSettings) -> list[str | None]: ...

    async def send(self, delivery: NotificationDelivery, notification: Notification) -> None: ...


class InAppChannel:
    name = NotificationChannelName.inapp
    requires_dispatch = False

    async def recipients(self, ctx: TenantContext, settings: NotificationSettings) -> list[str | None]:
        # Exactly one delivery row, no specific recipient — the inbox is the
        # whole tenant's, not per-user.
        return [None]

    async def send(self, delivery: NotificationDelivery, notification: Notification) -> None:
        # No-op — GET /notifications reads the Notification row directly,
        # so there's nothing to actually deliver for this channel.
        return None


class LineChannel:
    name = NotificationChannelName.line
    requires_dispatch = True

    async def recipients(self, ctx: TenantContext, settings: NotificationSettings) -> list[str | None]:
        if not settings.line_enabled:
            return []
        rows = await ctx.db.scalars(ctx.scoped(LineRecipient).where(LineRecipient.is_active.is_(True)))
        return [r.line_user_id for r in rows]

    async def send(self, delivery: NotificationDelivery, notification: Notification) -> None:
        assert delivery.recipient is not None  # line deliveries always have a recipient (see recipients() above)
        await line_service.push_message(delivery.recipient, f"{notification.title}\n{notification.body}")


CHANNELS: list[NotificationChannel] = [InAppChannel(), LineChannel()]
