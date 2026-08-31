"""Pluggable delivery channels for notifications — mirrors the
ProductLookupProvider Protocol pattern in app/ai/product_lookup_provider.py.
In-app is the only channel today; the Protocol is what let the retired LINE
push channel be added and later removed without touching
notification_service.py or notification_dispatch.py."""

from typing import Protocol

from app.core.tenancy import TenantContext
from app.models.notification import (
    Notification,
    NotificationChannelName,
    NotificationDelivery,
    NotificationSettings,
)


class NotificationChannel(Protocol):
    name: NotificationChannelName
    # False for channels where the notification row itself is sufficient
    # (in-app) — True for anything that makes an outbound call and should
    # be deferred by quiet hours / retried on failure.
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


CHANNELS: list[NotificationChannel] = [InAppChannel()]
