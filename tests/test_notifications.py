import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from .conftest import auth_headers, create_product, signup


async def _pin_login(client, headers_owner, tenant_id, role, pin):
    resp = await client.post("/api/v1/staff", json={"name": role, "role": role, "pin": pin}, headers=headers_owner)
    assert resp.status_code == 201, resp.text
    login = await client.post("/api/v1/auth/pin-login", json={"tenant_id": tenant_id, "pin": pin})
    assert login.status_code == 200, login.text
    return auth_headers(login.json())


async def _open_all_gates(client, headers):
    # low_stock_time / daily_summary_time default to 09:00 / 20:00 local —
    # pin them to midnight so the sweep never skips a test run just because
    # of what wall-clock time it happens to execute at.
    resp = await client.patch(
        "/api/v1/notifications/settings",
        json={"low_stock_time": "00:00:00", "daily_summary_time": "00:00:00"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


async def test_low_stock_job_creates_digest_and_dedupes_same_day(client):
    tokens = await signup(client, "Shop N1", "Owner", "notif-1@example.com")
    headers = auth_headers(tokens)
    await _open_all_gates(client, headers)
    # low_stock_threshold defaults to 5, so stock_qty=2 is already low.
    await create_product(client, headers, "สินค้าใกล้หมด", sell_price="10.00", stock_qty=2)

    first = await client.post("/api/v1/notifications/jobs/low_stock/run", headers=headers)
    assert first.status_code == 202, first.text

    inbox = await client.get("/api/v1/notifications", headers=headers)
    assert inbox.status_code == 200, inbox.text
    low_stock_notifs = [n for n in inbox.json() if n["kind"] == "low_stock"]
    assert len(low_stock_notifs) == 1
    assert "สินค้าใกล้หมด" in low_stock_notifs[0]["body"]

    second = await client.post("/api/v1/notifications/jobs/low_stock/run", headers=headers)
    assert second.status_code == 202, second.text

    inbox2 = await client.get("/api/v1/notifications", headers=headers)
    assert len([n for n in inbox2.json() if n["kind"] == "low_stock"]) == 1


async def test_low_stock_job_realerts_after_restock_and_redrop(client):
    tokens = await signup(client, "Shop N2", "Owner", "notif-2@example.com")
    headers = auth_headers(tokens)
    await _open_all_gates(client, headers)
    product = await create_product(client, headers, "สินค้า", sell_price="10.00", stock_qty=2)

    await client.post("/api/v1/notifications/jobs/low_stock/run", headers=headers)
    inbox = await client.get("/api/v1/notifications", headers=headers)
    assert len([n for n in inbox.json() if n["kind"] == "low_stock"]) == 1

    # Restock well above threshold, then sweep — recovers, no new notification.
    resp = await client.post(
        "/api/v1/inventory/adjust",
        json={"product_id": product["id"], "qty_delta": 50, "type": "purchase"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    await client.post("/api/v1/notifications/jobs/low_stock/run", headers=headers)
    inbox = await client.get("/api/v1/notifications", headers=headers)
    assert len([n for n in inbox.json() if n["kind"] == "low_stock"]) == 1  # unchanged

    # Drop below threshold again — since the alert-state row was deleted on
    # recovery, this re-alerts immediately instead of waiting out the
    # repeat window.
    resp = await client.post(
        "/api/v1/inventory/adjust",
        json={"product_id": product["id"], "qty_delta": -49, "type": "waste"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    await client.post("/api/v1/notifications/jobs/low_stock/run", headers=headers)
    inbox = await client.get("/api/v1/notifications", headers=headers)
    assert len([n for n in inbox.json() if n["kind"] == "low_stock"]) == 2


async def test_daily_summary_job_reports_todays_revenue(client):
    tokens = await signup(client, "Shop N3", "Owner", "notif-3@example.com")
    headers = auth_headers(tokens)
    await _open_all_gates(client, headers)
    product = await create_product(client, headers, "กาแฟ", sell_price="45.00", stock_qty=100)
    resp = await client.post(
        "/api/v1/sales",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"product_id": product["id"], "qty": 2}]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text

    run = await client.post("/api/v1/notifications/jobs/daily_summary/run", headers=headers)
    assert run.status_code == 202, run.text

    inbox = await client.get("/api/v1/notifications", headers=headers)
    summaries = [n for n in inbox.json() if n["kind"] == "daily_summary"]
    assert len(summaries) == 1
    assert summaries[0]["payload"]["revenue"] == "90.00"

    # Same local business day → deduped, not a second notification.
    await client.post("/api/v1/notifications/jobs/daily_summary/run", headers=headers)
    inbox2 = await client.get("/api/v1/notifications", headers=headers)
    assert len([n for n in inbox2.json() if n["kind"] == "daily_summary"]) == 1


async def test_daily_summary_reflects_daily_close_reason_not_zero_revenue(client):
    tokens = await signup(client, "Shop N4", "Owner", "notif-4@example.com")
    headers = auth_headers(tokens)
    await _open_all_gates(client, headers)

    today = datetime.now(ZoneInfo("Asia/Bangkok")).date().isoformat()
    resp = await client.post(
        "/api/v1/turbo/daily-close",
        json={"business_date": today, "closed_reason": "sick", "extra_expense": "0", "note": None},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text

    await client.post("/api/v1/notifications/jobs/daily_summary/run", headers=headers)
    inbox = await client.get("/api/v1/notifications", headers=headers)
    summaries = [n for n in inbox.json() if n["kind"] == "daily_summary"]
    assert len(summaries) == 1
    assert "ลาป่วย" in summaries[0]["title"]
    assert summaries[0]["payload"]["closed_reason"] == "sick"


async def test_mark_read_and_mark_all_read(client):
    tokens = await signup(client, "Shop N5", "Owner", "notif-5@example.com")
    headers = auth_headers(tokens)
    await _open_all_gates(client, headers)
    await create_product(client, headers, "สินค้า", sell_price="10.00", stock_qty=1)
    await client.post("/api/v1/notifications/jobs/low_stock/run", headers=headers)

    unread = await client.get("/api/v1/notifications/unread-count", headers=headers)
    assert unread.json()["unread_count"] == 1

    notif_id = (await client.get("/api/v1/notifications", headers=headers)).json()[0]["id"]
    read_resp = await client.post(f"/api/v1/notifications/{notif_id}/read", headers=headers)
    assert read_resp.status_code == 200, read_resp.text
    assert read_resp.json()["read_at"] is not None

    unread = await client.get("/api/v1/notifications/unread-count", headers=headers)
    assert unread.json()["unread_count"] == 0

    await create_product(client, headers, "สินค้า2", sell_price="10.00", stock_qty=1)
    await client.post("/api/v1/notifications/jobs/low_stock/run", headers=headers)
    unread = await client.get("/api/v1/notifications/unread-count", headers=headers)
    assert unread.json()["unread_count"] == 1

    read_all = await client.post("/api/v1/notifications/read-all", headers=headers)
    assert read_all.status_code == 204
    unread = await client.get("/api/v1/notifications/unread-count", headers=headers)
    assert unread.json()["unread_count"] == 0


async def test_settings_update_requires_manage_settings(client):
    tokens = await signup(client, "Shop N6", "Owner", "notif-6@example.com")
    headers_owner = auth_headers(tokens)
    me = await client.get("/api/v1/auth/me", headers=headers_owner)
    tenant_id = me.json()["tenant_id"]
    headers_cashier = await _pin_login(client, headers_owner, tenant_id, "cashier", "1234")

    resp = await client.patch(
        "/api/v1/notifications/settings", json={"low_stock_enabled": False}, headers=headers_cashier
    )
    assert resp.status_code == 403

    # A cashier can still read their own shop's inbox.
    resp = await client.get("/api/v1/notifications", headers=headers_cashier)
    assert resp.status_code == 200, resp.text


async def test_trigger_unknown_job_returns_400(client):
    tokens = await signup(client, "Shop N7", "Owner", "notif-7@example.com")
    headers = auth_headers(tokens)
    resp = await client.post("/api/v1/notifications/jobs/not-a-real-job/run", headers=headers)
    assert resp.status_code == 400


async def test_low_stock_disabled_skips_the_sweep(client):
    tokens = await signup(client, "Shop N8", "Owner", "notif-8@example.com")
    headers = auth_headers(tokens)
    await _open_all_gates(client, headers)
    resp = await client.patch(
        "/api/v1/notifications/settings", json={"low_stock_enabled": False}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    await create_product(client, headers, "สินค้า", sell_price="10.00", stock_qty=1)

    await client.post("/api/v1/notifications/jobs/low_stock/run", headers=headers)
    inbox = await client.get("/api/v1/notifications", headers=headers)
    assert len([n for n in inbox.json() if n["kind"] == "low_stock"]) == 0


async def test_notifications_are_isolated_per_tenant(client):
    tokens_a = await signup(client, "Shop N9a", "Owner", "notif-9a@example.com")
    headers_a = auth_headers(tokens_a)
    await _open_all_gates(client, headers_a)
    await create_product(client, headers_a, "สินค้า", sell_price="10.00", stock_qty=1)
    await client.post("/api/v1/notifications/jobs/low_stock/run", headers=headers_a)

    tokens_b = await signup(client, "Shop N9b", "Owner", "notif-9b@example.com")
    headers_b = auth_headers(tokens_b)
    inbox_b = await client.get("/api/v1/notifications", headers=headers_b)
    assert inbox_b.json() == []
