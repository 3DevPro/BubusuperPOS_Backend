"""Walks pending/retryable deliveries and hands each to its channel's
send(). Called two ways: inline from notification_service.create() right
after a notification is inserted (so a non-deferred delivery goes out
immediately instead of waiting for the next scheduler tick), and from the
scheduler's own dispatch sweep (app/jobs/scheduler.py) to catch anything
that was deferred by quiet hours or failed and is due for a retry."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import DeliveryStatus, Notification, NotificationDelivery
from app.services.notification_channels import CHANNELS

_MAX_ATTEMPTS = 3
_CHANNEL_BY_NAME = {channel.name: channel for channel in CHANNELS}


async def dispatch_pending(db: AsyncSession, notification_id: uuid.UUID | None = None) -> None:
    now = datetime.now(timezone.utc)
    query = select(NotificationDelivery).where(
        NotificationDelivery.status.in_([DeliveryStatus.pending, DeliveryStatus.failed]),
        NotificationDelivery.attempts < _MAX_ATTEMPTS,
        or_(NotificationDelivery.not_before.is_(None), NotificationDelivery.not_before <= now),
    )
    if notification_id is not None:
        query = query.where(NotificationDelivery.notification_id == notification_id)

    deliveries = list(await db.scalars(query))
    for delivery in deliveries:
        channel = _CHANNEL_BY_NAME.get(delivery.channel)
        delivery.attempts += 1
        if channel is None:
            delivery.status = DeliveryStatus.skipped
            delivery.last_error = f"no channel registered for {delivery.channel}"
            continue

        notification = await db.get(Notification, delivery.notification_id)
        try:
            await channel.send(delivery, notification)
            delivery.status = DeliveryStatus.sent
            delivery.sent_at = now
        except Exception as exc:  # noqa: BLE001 — a bad delivery must not abort the whole sweep
            delivery.status = DeliveryStatus.failed
            delivery.last_error = str(exc)[:500]

    await db.commit()
