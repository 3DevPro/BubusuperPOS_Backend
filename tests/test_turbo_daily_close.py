from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .conftest import auth_headers, signup

_TZ = ZoneInfo("Asia/Bangkok")


def _today():
    return datetime.now(_TZ).date()


async def test_close_day_defaults_to_open(client):
    tokens = await signup(client, "Shop A", "Owner A", "daily-close-a@example.com")
    headers = auth_headers(tokens)

    resp = await client.post(
        "/api/v1/turbo/daily-close", json={"business_date": str(_today())}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["closed_reason"] == "open"
    assert body["extra_expense"] == "0.00" or float(body["extra_expense"]) == 0
    assert body["business_date"] == str(_today())


async def test_close_day_with_reason_and_note(client):
    tokens = await signup(client, "Shop B", "Owner B", "daily-close-b@example.com")
    headers = auth_headers(tokens)

    resp = await client.post(
        "/api/v1/turbo/daily-close",
        json={
            "business_date": str(_today()),
            "closed_reason": "sick",
            "extra_expense": "50.00",
            "note": "ไม่สบาย",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["closed_reason"] == "sick"
    assert float(body["extra_expense"]) == 50.0
    assert body["note"] == "ไม่สบาย"


async def test_closing_same_date_twice_upserts_instead_of_duplicating(client):
    tokens = await signup(client, "Shop C", "Owner C", "daily-close-c@example.com")
    headers = auth_headers(tokens)

    first = await client.post(
        "/api/v1/turbo/daily-close",
        json={"business_date": str(_today()), "closed_reason": "open"},
        headers=headers,
    )
    assert first.status_code == 201

    second = await client.post(
        "/api/v1/turbo/daily-close",
        json={"business_date": str(_today()), "closed_reason": "accident", "note": "corrected"},
        headers=headers,
    )
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["closed_reason"] == "accident"

    listed = await client.get("/api/v1/turbo/daily-close", headers=headers)
    matching = [row for row in listed.json() if row["business_date"] == str(_today())]
    assert len(matching) == 1
    assert matching[0]["closed_reason"] == "accident"


async def test_future_business_date_is_rejected(client):
    tokens = await signup(client, "Shop D", "Owner D", "daily-close-d@example.com")
    headers = auth_headers(tokens)

    tomorrow = _today() + timedelta(days=1)
    resp = await client.post(
        "/api/v1/turbo/daily-close", json={"business_date": str(tomorrow)}, headers=headers
    )
    assert resp.status_code == 400


async def test_list_is_scoped_to_tenant(client):
    tokens_a = await signup(client, "Shop E", "Owner E", "daily-close-e@example.com")
    headers_a = auth_headers(tokens_a)
    await client.post(
        "/api/v1/turbo/daily-close", json={"business_date": str(_today())}, headers=headers_a
    )

    tokens_b = await signup(client, "Shop F", "Owner F", "daily-close-f@example.com")
    headers_b = auth_headers(tokens_b)

    listed_b = await client.get("/api/v1/turbo/daily-close", headers=headers_b)
    assert listed_b.status_code == 200
    assert listed_b.json() == []


async def test_days_window_filters_older_closes(client):
    tokens = await signup(client, "Shop G", "Owner G", "daily-close-g@example.com")
    headers = auth_headers(tokens)

    old_date = _today() - timedelta(days=5)
    await client.post(
        "/api/v1/turbo/daily-close", json={"business_date": str(old_date)}, headers=headers
    )
    await client.post(
        "/api/v1/turbo/daily-close", json={"business_date": str(_today())}, headers=headers
    )

    narrow = await client.get("/api/v1/turbo/daily-close", params={"days": 1}, headers=headers)
    assert narrow.status_code == 200
    narrow_dates = {row["business_date"] for row in narrow.json()}
    assert narrow_dates == {str(_today())}

    wide = await client.get("/api/v1/turbo/daily-close", params={"days": 10}, headers=headers)
    wide_dates = {row["business_date"] for row in wide.json()}
    assert wide_dates == {str(_today()), str(old_date)}


async def test_cashier_can_close_the_shop(client):
    """Closing out at end-of-shift is a normal register action, not a
    back-office one — a cashier (not just owner/manager) must be able to do
    it, same as they can already create_sale."""
    tokens = await signup(client, "Shop H", "Owner H", "daily-close-h@example.com")
    owner_headers = auth_headers(tokens)

    me = await client.get("/api/v1/auth/me", headers=owner_headers)
    tenant_id = me.json()["tenant_id"]

    staff_resp = await client.post(
        "/api/v1/staff",
        json={"name": "Cashier H", "role": "cashier", "pin": "1234"},
        headers=owner_headers,
    )
    assert staff_resp.status_code == 201, staff_resp.text

    pin_login = await client.post("/api/v1/auth/pin-login", json={"tenant_id": tenant_id, "pin": "1234"})
    assert pin_login.status_code == 200, pin_login.text
    cashier_headers = auth_headers(pin_login.json())

    resp = await client.post(
        "/api/v1/turbo/daily-close", json={"business_date": str(_today())}, headers=cashier_headers
    )
    assert resp.status_code == 201, resp.text
