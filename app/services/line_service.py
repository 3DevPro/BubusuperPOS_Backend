"""LINE Messaging API integration — account linking (a 6-character code
typed into the OA chat, see create_link_token) and the push/reply calls that
back LineChannel in notification_channels.py. Mirrors the per-call
httpx.AsyncClient idiom in app/ai/product_lookup_provider.py rather than a
shared pooled client, since this isn't a hot path.

Webhook events must never propagate an exception up to the endpoint — LINE
disables a webhook after repeated non-200 responses, so a malformed or
unexpected event is logged and skipped, not raised."""

import hashlib
import hmac
import logging
import secrets
import string
import uuid
from base64 import b64encode
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.tenancy import TenantContext
from app.models.notification import LineLinkToken, LineRecipient
from app.models.tenant import Tenant

logger = logging.getLogger(__name__)

_LINK_TOKEN_TTL = timedelta(minutes=15)
# Excludes visually-ambiguous characters (0/O, 1/I/L) since this code is
# read off a phone screen and typed into a chat.
_TOKEN_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_TOKEN_LENGTH = 6

_PUSH_URL = "https://api.line.me/v2/bot/message/push"
_REPLY_URL = "https://api.line.me/v2/bot/message/reply"


def verify_signature(body: bytes, signature: str | None) -> bool:
    if not signature or not settings.line_channel_secret:
        return False
    digest = hmac.new(settings.line_channel_secret.encode(), body, hashlib.sha256).digest()
    expected = b64encode(digest).decode()
    return hmac.compare_digest(expected, signature)


def _generate_token() -> str:
    return "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(_TOKEN_LENGTH))


async def create_link_token(ctx: TenantContext) -> LineLinkToken:
    token = LineLinkToken(
        token=_generate_token(),
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        expires_at=datetime.now(timezone.utc) + _LINK_TOKEN_TTL,
    )
    ctx.db.add(token)
    await ctx.db.commit()
    await ctx.db.refresh(token)
    return token


def oa_add_friend_url() -> str | None:
    if not settings.line_oa_basic_id:
        return None
    return f"https://line.me/R/ti/p/{settings.line_oa_basic_id}"


async def list_recipients(ctx: TenantContext) -> list[LineRecipient]:
    result = await ctx.db.scalars(ctx.scoped(LineRecipient).order_by(LineRecipient.linked_at.desc()))
    return list(result)


async def unlink(ctx: TenantContext, recipient_id: uuid.UUID) -> None:
    recipient = await ctx.db.scalar(ctx.scoped(LineRecipient).where(LineRecipient.id == recipient_id))
    if recipient is not None:
        recipient.is_active = False
        await ctx.db.commit()


async def _consume_link_token(db: AsyncSession, code: str, line_user_id: str) -> Tenant | None:
    now = datetime.now(timezone.utc)
    token = await db.scalar(
        select(LineLinkToken).where(
            LineLinkToken.token == code, LineLinkToken.used_at.is_(None), LineLinkToken.expires_at > now
        )
    )
    if token is None:
        return None

    # One LINE account can only ever be linked to one shop — re-scanning a
    # new code for the same LINE account re-points it rather than erroring,
    # which is the sane outcome if an owner accidentally links the wrong
    # shop and corrects it.
    await db.execute(
        pg_insert(LineRecipient)
        .values(
            tenant_id=token.tenant_id,
            line_user_id=line_user_id,
            user_id=token.user_id,
            is_active=True,
        )
        .on_conflict_do_update(
            index_elements=["line_user_id"],
            set_={"tenant_id": token.tenant_id, "user_id": token.user_id, "is_active": True},
        )
    )
    token.used_at = now
    tenant = await db.get(Tenant, token.tenant_id)
    await db.commit()
    return tenant


async def handle_webhook_events(db: AsyncSession, events: list[dict]) -> None:
    for event in events:
        try:
            await _handle_event(db, event)
        except Exception:
            logger.exception("failed to handle LINE webhook event: %r", event)


async def _handle_event(db: AsyncSession, event: dict) -> None:
    event_type = event.get("type")
    source = event.get("source") or {}
    line_user_id = source.get("userId")
    reply_token = event.get("replyToken")

    if event_type == "message" and (event.get("message") or {}).get("type") == "text" and line_user_id:
        code = event["message"]["text"].strip().upper()
        tenant = await _consume_link_token(db, code, line_user_id)
        if tenant is not None:
            if reply_token:
                await reply_message(reply_token, f"เชื่อมต่อร้าน {tenant.name} เรียบร้อยแล้ว ✅")
        elif reply_token:
            await reply_message(reply_token, "รหัสไม่ถูกต้องหรือหมดอายุ กรุณาขอรหัสใหม่จากหน้าตั้งค่าในแอป")
        return

    if event_type == "follow" and reply_token:
        await reply_message(reply_token, "ยินดีต้อนรับ! พิมพ์รหัส 6 หลักจากหน้าตั้งค่าในแอปเพื่อเชื่อมต่อร้านของคุณ")
        return

    if event_type == "unfollow" and line_user_id:
        recipient = await db.scalar(select(LineRecipient).where(LineRecipient.line_user_id == line_user_id))
        if recipient is not None:
            recipient.is_active = False
            await db.commit()


async def _post(url: str, payload: dict) -> None:
    if not settings.line_channel_access_token:
        raise RuntimeError("LINE_CHANNEL_ACCESS_TOKEN is not configured")
    async with httpx.AsyncClient(timeout=8) as client:
        resp = await client.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {settings.line_channel_access_token}"},
        )
        resp.raise_for_status()


async def push_message(line_user_id: str, text: str) -> None:
    await _post(_PUSH_URL, {"to": line_user_id, "messages": [{"type": "text", "text": text}]})


async def reply_message(reply_token: str, text: str) -> None:
    # Doesn't count against the push-message quota — used for the direct
    # response to an incoming webhook event, which always has a reply token.
    try:
        await _post(_REPLY_URL, {"replyToken": reply_token, "messages": [{"type": "text", "text": text}]})
    except Exception:
        # Best-effort — a failed reply (e.g. the token already expired)
        # must not stop the rest of webhook event handling.
        logger.exception("failed to reply to LINE event")
