import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import CurrentTenant, get_tenant_context, require
from app.core.permissions import Permission
from app.core.tenancy import TenantContext
from app.jobs.scheduler import run_job
from app.models.notification import LineRecipient, Notification, NotificationSettings
from app.schemas.notification import (
    LineLinkTokenResponse,
    LineRecipientResponse,
    NotificationResponse,
    NotificationSettingsResponse,
    NotificationSettingsUpdateRequest,
)
from app.services import line_service, notification_service

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


@router.post("/line/webhook")
async def line_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    # No auth dependency — LINE calls this directly. Must read the raw body
    # before any parsing: the signature is computed over the exact bytes
    # LINE sent, and letting FastAPI parse the body first (e.g. via a
    # Pydantic model param) would make that raw payload unrecoverable here.
    body = await request.body()
    if not line_service.verify_signature(body, request.headers.get("X-Line-Signature")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid signature")

    payload = json.loads(body)
    await line_service.handle_webhook_events(db, payload.get("events", []))
    # Always 200 once the signature checks out — LINE retries and eventually
    # disables a webhook that doesn't return 200, and a bad individual event
    # is already isolated inside handle_webhook_events.
    return {"status": "ok"}


@router.post("/line/link-token", response_model=LineLinkTokenResponse)
async def create_line_link_token(
    ctx: TenantContext = Depends(require(Permission.manage_settings)),
) -> LineLinkTokenResponse:
    add_friend_url = line_service.oa_add_friend_url()
    if add_friend_url is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "LINE OA ยังไม่ได้ตั้งค่าในระบบ")
    token = await line_service.create_link_token(ctx)
    return LineLinkTokenResponse(token=token.token, oa_add_friend_url=add_friend_url, expires_at=token.expires_at)


@router.get("/line/recipients", response_model=list[LineRecipientResponse])
async def list_line_recipients(
    ctx: TenantContext = Depends(require(Permission.manage_settings)),
) -> list[LineRecipient]:
    return await line_service.list_recipients(ctx)


@router.delete("/line/recipients/{recipient_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_line_recipient(
    recipient_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.manage_settings)),
) -> None:
    await line_service.unlink(ctx, recipient_id)
