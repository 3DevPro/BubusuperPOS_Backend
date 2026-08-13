"""In-app notification inbox plus fan-out to whatever channels are
registered (see notification_channels.py). Notification creation is
idempotent via dedupe_key — see the class docstring on Notification — which
is what lets the scheduler jobs in app/jobs/ run a poll-and-check sweep
every 15 minutes without ever double-notifying."""

import uuid
from datetime import date, datetime, time, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.tenancy import TenantContext
from app.models.notification import Notification, NotificationDelivery, NotificationKind, NotificationSettings
from app.schemas.notification import NotificationSettingsUpdateRequest
from app.services.notification_channels import CHANNELS
from app.services.notification_dispatch import dispatch_pending
from app.services.report_service import tenant_local_timezone


async def get_settings(ctx: TenantContext) -> NotificationSettings:
    """Lazily creates a default-settings row on first access rather than at
    signup — signup shouldn't need to know this feature exists."""
    settings = await ctx.db.scalar(ctx.scoped(NotificationSettings))
    if settings is not None:
        return settings

    settings = NotificationSettings(tenant_id=ctx.tenant_id)
    ctx.db.add(settings)
    await ctx.db.commit()
    await ctx.db.refresh(settings)
    return settings


async def update_settings(ctx: TenantContext, body: NotificationSettingsUpdateRequest) -> NotificationSettings:
    settings = await get_settings(ctx)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(settings, field, value)
    await ctx.db.commit()
    await ctx.db.refresh(settings)
    return settings


def _time_in_window(t: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= t < end
    return t >= start or t < end  # window wraps past midnight


async def _quiet_hours_not_before(ctx: TenantContext, settings: NotificationSettings) -> datetime | None:
    """None unless "right now" falls inside the tenant's configured quiet
    hours, in which case it's the UTC instant the window next opens. Only
    applied to channels that actually push (see NotificationChannel.
    requires_dispatch) — the in-app inbox row is never deferred."""
    if settings.quiet_hours_start is None or settings.quiet_hours_end is None:
        return None

    tz = await tenant_local_timezone(ctx)
    now_local = datetime.now(tz)
    if not _time_in_window(now_local.time(), settings.quiet_hours_start, settings.quiet_hours_end):
        return None

    end_local = datetime.combine(now_local.date(), settings.quiet_hours_end, tzinfo=tz)
    if end_local <= now_local:
        end_local += timedelta(days=1)
    return end_local.astimezone(timezone.utc)


async def create(
    ctx: TenantContext,
    kind: NotificationKind,
    title: str,
    body: str,
    dedupe_key: str,
    payload: dict | None = None,
    user_id: uuid.UUID | None = None,
) -> Notification | None:
    """Returns None when dedupe_key already exists for this tenant — the
    caller (a scheduler job) treats that as "already notified, nothing to
    do" rather than an error."""
    result = await ctx.db.execute(
        pg_insert(Notification)
        .values(
            tenant_id=ctx.tenant_id,
            user_id=user_id,
            kind=kind,
            title=title,
            body=body,
            payload=payload or {},
            dedupe_key=dedupe_key,
        )
        .on_conflict_do_nothing(index_elements=["tenant_id", "dedupe_key"])
        .returning(Notification)
    )
    notification = result.scalar_one_or_none()
    if notification is None:
        await ctx.db.commit()
        return None

    settings = await get_settings(ctx)
    not_before = await _quiet_hours_not_before(ctx, settings)

    for channel in CHANNELS:
        recipients = await channel.recipients(ctx, settings)
        for recipient in recipients:
            await ctx.db.execute(
                pg_insert(NotificationDelivery)
                .values(
                    tenant_id=ctx.tenant_id,
                    notification_id=notification.id,
                    channel=channel.name,
                    recipient=recipient,
                    not_before=not_before if channel.requires_dispatch else None,
                )
                .on_conflict_do_nothing(
                    index_elements=["notification_id", "channel", "recipient"]
                )
            )

    await ctx.db.commit()
    await dispatch_pending(ctx.db, notification_id=notification.id)
    return notification


async def list_inbox(ctx: TenantContext, unread_only: bool = False, limit: int = 50) -> list[Notification]:
    query = ctx.scoped(Notification).order_by(Notification.created_at.desc()).limit(limit)
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    result = await ctx.db.scalars(query)
    return list(result)


async def unread_count(ctx: TenantContext) -> int:
    result = await ctx.db.scalar(
        select(func.count(Notification.id)).where(
            Notification.tenant_id == ctx.tenant_id, Notification.read_at.is_(None)
        )
    )
    return result or 0


async def mark_read(ctx: TenantContext, notification_id: uuid.UUID) -> Notification:
    notification = await ctx.db.scalar(ctx.scoped(Notification).where(Notification.id == notification_id))
    if notification is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "notification not found")
    if notification.read_at is None:
        notification.read_at = datetime.now(timezone.utc)
        await ctx.db.commit()
        await ctx.db.refresh(notification)
    return notification


async def mark_all_read(ctx: TenantContext) -> None:
    await ctx.db.execute(
        update(Notification)
        .where(Notification.tenant_id == ctx.tenant_id, Notification.read_at.is_(None))
        .values(read_at=func.now())
    )
    await ctx.db.commit()


