import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.deps import CurrentTenant, get_tenant_context, require
from app.core.permissions import Permission
from app.core.tenancy import TenantContext
from app.jobs.scheduler import run_job
from app.models.notification import Notification, NotificationSettings
from app.schemas.notification import (
    NotificationResponse,
    NotificationSettingsResponse,
    NotificationSettingsUpdateRequest,
)
from app.services import notification_service

router = APIRouter(prefix="/notifications", tags=["notifications"])

_VALID_JOBS = {"low_stock", "daily_summary", "dispatch"}


@router.get("", response_model=list[NotificationResponse])
async def list_notifications(
    unread_only: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[Notification]:
    return await notification_service.list_inbox(ctx, unread_only=unread_only, limit=limit)


@router.get("/unread-count")
async def get_unread_count(ctx: CurrentTenant) -> dict[str, int]:
    return {"unread_count": await notification_service.unread_count(ctx)}


@router.post("/{notification_id}/read", response_model=NotificationResponse)
async def read_notification(notification_id: uuid.UUID, ctx: CurrentTenant) -> Notification:
    return await notification_service.mark_read(ctx, notification_id)


@router.post("/read-all", status_code=status.HTTP_204_NO_CONTENT)
async def read_all_notifications(ctx: CurrentTenant) -> None:
    await notification_service.mark_all_read(ctx)


@router.get("/settings", response_model=NotificationSettingsResponse)
async def get_notification_settings(ctx: CurrentTenant) -> NotificationSettings:
    return await notification_service.get_settings(ctx)


@router.patch("/settings", response_model=NotificationSettingsResponse)
async def update_notification_settings(
    body: NotificationSettingsUpdateRequest,
    ctx: TenantContext = Depends(require(Permission.manage_settings)),
) -> NotificationSettings:
    return await notification_service.update_settings(ctx, body)


@router.post("/jobs/{job}/run", status_code=status.HTTP_202_ACCEPTED)
async def trigger_job(
    job: str,
    ctx: TenantContext = Depends(require(Permission.manage_settings)),
) -> dict[str, str]:
    # Manual/debug trigger — also the escape hatch back to a host-crontab
    # driver if in-process scheduling ever proves wrong (see
    # app/jobs/scheduler.py). Runs the sweep across every tenant, not just
    # ctx.tenant_id — a low-stock digest is a global sweep by design so one
    # owner testing the button doesn't need every other shop to also be due.
    if job not in _VALID_JOBS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown job: {job}")
    await run_job(job)
    return {"status": "ran", "job": job}
