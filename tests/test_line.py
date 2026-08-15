import base64
import hashlib
import hmac
import json

from app.core.config import settings
from app.services import line_service

from .conftest import auth_headers, signup


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


async def _pin_login(client, headers_owner, tenant_id, role, pin):
    resp = await client.post("/api/v1/staff", json={"name": role, "role": role, "pin": pin}, headers=headers_owner)
    assert resp.status_code == 201, resp.text
    login = await client.post("/api/v1/auth/pin-login", json={"tenant_id": tenant_id, "pin": pin})
    assert login.status_code == 200, login.text
    return auth_headers(login.json())


def _configure_line(monkeypatch, secret="test-channel-secret", basic_id="@testoa", access_token="test-token"):
    monkeypatch.setattr(settings, "line_channel_secret", secret)
    monkeypatch.setattr(settings, "line_oa_basic_id", basic_id)
    monkeypatch.setattr(settings, "line_channel_access_token", access_token)


def _stub_outbound(monkeypatch):
    """push_message / reply_message make real HTTP calls to api.line.me —
    tests stub them out and record calls instead of hitting the network."""
    calls = {"push": [], "reply": []}

    async def fake_push(line_user_id, text):
        calls["push"].append((line_user_id, text))

    async def fake_reply(reply_token, text):
        calls["reply"].append((reply_token, text))

    monkeypatch.setattr(line_service, "push_message", fake_push)
    monkeypatch.setattr(line_service, "reply_message", fake_reply)
    return calls


def test_verify_signature_accepts_valid_and_rejects_tampered(monkeypatch):
    monkeypatch.setattr(settings, "line_channel_secret", "my-secret")
    body = b'{"events":[]}'
    good_signature = _sign(body, "my-secret")

    assert line_service.verify_signature(body, good_signature) is True
    assert line_service.verify_signature(body, _sign(body, "wrong-secret")) is False
    assert line_service.verify_signature(body, None) is False
    assert line_service.verify_signature(b"tampered body", good_signature) is False


def test_verify_signature_false_when_channel_secret_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "line_channel_secret", "")
    body = b"{}"
    assert line_service.verify_signature(body, _sign(body, "anything")) is False


async def test_create_link_token_requires_manage_settings(client, monkeypatch):
    _configure_line(monkeypatch)
    tokens = await signup(client, "Line Shop A", "Owner", "line-a@example.com")
    headers_owner = auth_headers(tokens)
    me = await client.get("/api/v1/auth/me", headers=headers_owner)
    tenant_id = me.json()["tenant_id"]
    headers_cashier = await _pin_login(client, headers_owner, tenant_id, "cashier", "1234")

    resp = await client.post("/api/v1/notifications/line/link-token", headers=headers_cashier)
    assert resp.status_code == 403


async def test_create_link_token_without_oa_configured_returns_503(client, monkeypatch):
    monkeypatch.setattr(settings, "line_oa_basic_id", "")
    tokens = await signup(client, "Line Shop B", "Owner", "line-b@example.com")
    resp = await client.post("/api/v1/notifications/line/link-token", headers=auth_headers(tokens))
    assert resp.status_code == 503


async def test_link_token_response_shape(client, monkeypatch):
    _configure_line(monkeypatch)
    tokens = await signup(client, "Line Shop C", "Owner", "line-c@example.com")
    resp = await client.post("/api/v1/notifications/line/link-token", headers=auth_headers(tokens))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["token"]) == 6
    assert body["oa_add_friend_url"] == "https://line.me/R/ti/p/@testoa"
    assert body["expires_at"]


async def test_webhook_rejects_missing_or_bad_signature(client, monkeypatch):
    _configure_line(monkeypatch)
    body = json.dumps({"events": []}).encode()

    resp = await client.post("/api/v1/notifications/line/webhook", content=body)
    assert resp.status_code == 400

    resp = await client.post(
        "/api/v1/notifications/line/webhook", content=body, headers={"X-Line-Signature": "not-a-real-signature"}
    )
    assert resp.status_code == 400


async def test_webhook_links_account_on_valid_code_and_replies(client, monkeypatch):
    _configure_line(monkeypatch)
    calls = _stub_outbound(monkeypatch)
    tokens = await signup(client, "Line Shop D", "Owner", "line-d@example.com")
    headers = auth_headers(tokens)

    link = await client.post("/api/v1/notifications/line/link-token", headers=headers)
    code = link.json()["token"]

    event_body = json.dumps(
        {
            "events": [
                {
                    "type": "message",
                    "replyToken": "reply-token-1",
                    "source": {"type": "user", "userId": "U1234567890"},
                    "message": {"type": "text", "id": "1", "text": code},
                }
            ]
        }
    ).encode()
    signature = _sign(event_body, settings.line_channel_secret)

    resp = await client.post(
        "/api/v1/notifications/line/webhook", content=event_body, headers={"X-Line-Signature": signature}
    )
    assert resp.status_code == 200, resp.text

    recipients = await client.get("/api/v1/notifications/line/recipients", headers=headers)
    assert recipients.status_code == 200, recipients.text
    assert len(recipients.json()) == 1
    assert recipients.json()[0]["is_active"] is True

    assert len(calls["reply"]) == 1
    assert calls["reply"][0][0] == "reply-token-1"
    assert "Line Shop D" in calls["reply"][0][1]

    # The code is single-use — replaying it must not create a duplicate link.
    resp2 = await client.post(
        "/api/v1/notifications/line/webhook", content=event_body, headers={"X-Line-Signature": signature}
    )
    assert resp2.status_code == 200
    recipients2 = await client.get("/api/v1/notifications/line/recipients", headers=headers)
    assert len(recipients2.json()) == 1
    assert len(calls["reply"]) == 2  # replied again, but with the "invalid/expired" message
    assert "ไม่ถูกต้อง" in calls["reply"][1][1] or "หมดอายุ" in calls["reply"][1][1]


async def test_webhook_plain_text_from_unlinked_user_replies_help(client, monkeypatch):
    _configure_line(monkeypatch)
    calls = _stub_outbound(monkeypatch)
    await signup(client, "Line Shop I", "Owner", "line-i@example.com")

    body = json.dumps(
        {
            "events": [
                {
                    "type": "message",
                    "replyToken": "rt-greet",
                    "source": {"type": "user", "userId": "U_GREETING"},
                    "message": {"type": "text", "id": "1", "text": "สวัสดีครับ"},
                }
            ]
        }
    ).encode()
    resp = await client.post(
        "/api/v1/notifications/line/webhook",
        content=body,
        headers={"X-Line-Signature": _sign(body, settings.line_channel_secret)},
    )
    assert resp.status_code == 200

    assert len(calls["reply"]) == 1
    assert "ไม่ถูกต้อง" not in calls["reply"][0][1]
    assert "หมดอายุ" not in calls["reply"][0][1]


async def test_webhook_plain_text_from_linked_user_replies_shop_status(client, monkeypatch):
    _configure_line(monkeypatch)
    calls = _stub_outbound(monkeypatch)
    tokens = await signup(client, "Line Shop J", "Owner", "line-j@example.com")
    headers = auth_headers(tokens)
    link = await client.post("/api/v1/notifications/line/link-token", headers=headers)
    code = link.json()["token"]

    link_body = json.dumps(
        {
            "events": [
                {
                    "type": "message",
                    "replyToken": "rt1",
                    "source": {"type": "user", "userId": "U_ASKING"},
                    "message": {"type": "text", "id": "1", "text": code},
                }
            ]
        }
    ).encode()
    await client.post(
        "/api/v1/notifications/line/webhook",
        content=link_body,
        headers={"X-Line-Signature": _sign(link_body, settings.line_channel_secret)},
    )

    ask_body = json.dumps(
        {
            "events": [
                {
                    "type": "message",
                    "replyToken": "rt2",
                    "source": {"type": "user", "userId": "U_ASKING"},
                    "message": {"type": "text", "id": "2", "text": "ยอดขายวันนี้เท่าไหร่"},
                }
            ]
        }
    ).encode()
    resp = await client.post(
        "/api/v1/notifications/line/webhook",
        content=ask_body,
        headers={"X-Line-Signature": _sign(ask_body, settings.line_channel_secret)},
    )
    assert resp.status_code == 200

    assert len(calls["reply"]) == 2
    assert "Line Shop J" in calls["reply"][1][1]
    assert "ไม่ถูกต้อง" not in calls["reply"][1][1]


async def test_webhook_code_shaped_but_invalid_still_replies_error(client, monkeypatch):
    _configure_line(monkeypatch)
    calls = _stub_outbound(monkeypatch)
    await signup(client, "Line Shop K", "Owner", "line-k@example.com")

    body = json.dumps(
        {
            "events": [
                {
                    "type": "message",
                    "replyToken": "rt-bad-code",
                    "source": {"type": "user", "userId": "U_BADCODE"},
                    "message": {"type": "text", "id": "1", "text": "ZZZZZZ"},
                }
            ]
        }
    ).encode()
    resp = await client.post(
        "/api/v1/notifications/line/webhook",
        content=body,
        headers={"X-Line-Signature": _sign(body, settings.line_channel_secret)},
    )
    assert resp.status_code == 200

    assert len(calls["reply"]) == 1
    assert "ไม่ถูกต้อง" in calls["reply"][0][1] or "หมดอายุ" in calls["reply"][0][1]


async def test_webhook_follow_event_reflects_link_state(client, monkeypatch):
    _configure_line(monkeypatch)
    calls = _stub_outbound(monkeypatch)
    tokens = await signup(client, "Line Shop L", "Owner", "line-l@example.com")
    headers = auth_headers(tokens)
    link = await client.post("/api/v1/notifications/line/link-token", headers=headers)
    code = link.json()["token"]

    link_body = json.dumps(
        {
            "events": [
                {
                    "type": "message",
                    "replyToken": "rt1",
                    "source": {"type": "user", "userId": "U_FOLLOW"},
                    "message": {"type": "text", "id": "1", "text": code},
                }
            ]
        }
    ).encode()
    await client.post(
        "/api/v1/notifications/line/webhook",
        content=link_body,
        headers={"X-Line-Signature": _sign(link_body, settings.line_channel_secret)},
    )

    follow_body = json.dumps(
        {
            "events": [
                {
                    "type": "follow",
                    "replyToken": "rt-follow",
                    "source": {"type": "user", "userId": "U_FOLLOW"},
                }
            ]
        }
    ).encode()
    resp = await client.post(
        "/api/v1/notifications/line/webhook",
        content=follow_body,
        headers={"X-Line-Signature": _sign(follow_body, settings.line_channel_secret)},
    )
    assert resp.status_code == 200

    assert len(calls["reply"]) == 2
    assert "Line Shop L" in calls["reply"][1][1]
    assert "พิมพ์รหัส 6 หลัก" not in calls["reply"][1][1]


async def test_webhook_unfollow_deactivates_recipient(client, monkeypatch):
    _configure_line(monkeypatch)
    calls = _stub_outbound(monkeypatch)
    tokens = await signup(client, "Line Shop E", "Owner", "line-e@example.com")
    headers = auth_headers(tokens)
    link = await client.post("/api/v1/notifications/line/link-token", headers=headers)
    code = link.json()["token"]

    link_body = json.dumps(
        {
            "events": [
                {
                    "type": "message",
                    "replyToken": "rt1",
                    "source": {"type": "user", "userId": "U_UNFOLLOW"},
                    "message": {"type": "text", "id": "1", "text": code},
                }
            ]
        }
    ).encode()
    await client.post(
        "/api/v1/notifications/line/webhook",
        content=link_body,
        headers={"X-Line-Signature": _sign(link_body, settings.line_channel_secret)},
    )

    unfollow_body = json.dumps({"events": [{"type": "unfollow", "source": {"userId": "U_UNFOLLOW"}}]}).encode()
    resp = await client.post(
        "/api/v1/notifications/line/webhook",
        content=unfollow_body,
        headers={"X-Line-Signature": _sign(unfollow_body, settings.line_channel_secret)},
    )
    assert resp.status_code == 200

    recipients = (await client.get("/api/v1/notifications/line/recipients", headers=headers)).json()
    assert recipients[0]["is_active"] is False
    assert calls  # sanity: outbound stub was actually wired


async def test_unlink_deactivates_recipient(client, monkeypatch):
    _configure_line(monkeypatch)
    _stub_outbound(monkeypatch)
    tokens = await signup(client, "Line Shop F", "Owner", "line-f@example.com")
    headers = auth_headers(tokens)
    link = await client.post("/api/v1/notifications/line/link-token", headers=headers)
    code = link.json()["token"]
    body = json.dumps(
        {
            "events": [
                {
                    "type": "message",
                    "replyToken": "rt",
                    "source": {"type": "user", "userId": "U_UNLINK"},
                    "message": {"type": "text", "id": "1", "text": code},
                }
            ]
        }
    ).encode()
    await client.post(
        "/api/v1/notifications/line/webhook",
        content=body,
        headers={"X-Line-Signature": _sign(body, settings.line_channel_secret)},
    )
    recipient_id = (await client.get("/api/v1/notifications/line/recipients", headers=headers)).json()[0]["id"]

    resp = await client.delete(f"/api/v1/notifications/line/recipients/{recipient_id}", headers=headers)
    assert resp.status_code == 204

    recipients = (await client.get("/api/v1/notifications/line/recipients", headers=headers)).json()
    assert recipients[0]["is_active"] is False


async def test_low_stock_dispatch_pushes_to_linked_line_account(client, monkeypatch):
    _configure_line(monkeypatch)
    calls = _stub_outbound(monkeypatch)
    tokens = await signup(client, "Line Shop G", "Owner", "line-g@example.com")
    headers = auth_headers(tokens)

    # Link a LINE account.
    link = await client.post("/api/v1/notifications/line/link-token", headers=headers)
    code = link.json()["token"]
    link_body = json.dumps(
        {
            "events": [
                {
                    "type": "message",
                    "replyToken": "rt",
                    "source": {"type": "user", "userId": "U_DISPATCH"},
                    "message": {"type": "text", "id": "1", "text": code},
                }
            ]
        }
    ).encode()
    await client.post(
        "/api/v1/notifications/line/webhook",
        content=link_body,
        headers={"X-Line-Signature": _sign(link_body, settings.line_channel_secret)},
    )

    # Opt the tenant into LINE push, and open the low-stock gate.
    resp = await client.patch(
        "/api/v1/notifications/settings",
        json={"line_enabled": True, "low_stock_time": "00:00:00"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    from .conftest import create_product

    await create_product(client, headers, "ของใกล้หมด", sell_price="10.00", stock_qty=1)

    run = await client.post("/api/v1/notifications/jobs/low_stock/run", headers=headers)
    assert run.status_code == 202, run.text

    assert len(calls["push"]) == 1
    assert calls["push"][0][0] == "U_DISPATCH"
    assert "สินค้าใกล้หมด" in calls["push"][0][1]


async def test_line_recipients_isolated_per_tenant(client, monkeypatch):
    _configure_line(monkeypatch)
    _stub_outbound(monkeypatch)
    tokens_a = await signup(client, "Line Shop H1", "Owner", "line-h1@example.com")
    tokens_b = await signup(client, "Line Shop H2", "Owner", "line-h2@example.com")

    link = await client.post("/api/v1/notifications/line/link-token", headers=auth_headers(tokens_a))
    code = link.json()["token"]
    body = json.dumps(
        {
            "events": [
                {
                    "type": "message",
                    "replyToken": "rt",
                    "source": {"type": "user", "userId": "U_ISOLATED"},
                    "message": {"type": "text", "id": "1", "text": code},
                }
            ]
        }
    ).encode()
    await client.post(
        "/api/v1/notifications/line/webhook",
        content=body,
        headers={"X-Line-Signature": _sign(body, settings.line_channel_secret)},
    )

    recipients_b = await client.get("/api/v1/notifications/line/recipients", headers=auth_headers(tokens_b))
    assert recipients_b.json() == []
