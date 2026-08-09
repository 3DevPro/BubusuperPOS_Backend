import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.sale import PaymentMethod, Sale, SaleStatus

from .conftest import auth_headers, signup

_TZ = ZoneInfo("Asia/Bangkok")


def _today():
    return datetime.now(_TZ).date()


async def _close(client, headers, business_date, reason="open"):
    resp = await client.post(
        "/api/v1/turbo/daily-close",
        json={"business_date": str(business_date), "closed_reason": reason},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_empty_profile_has_no_streak_and_locked_credit(client):
    tokens = await signup(client, "Shop A", "Owner A", "income-a@example.com")
    headers = auth_headers(tokens)

    resp = await client.get("/api/v1/turbo/income-profile", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["streak_days"] == 0
    assert body["days_recorded"] == 0
    assert body["credit_tier"] == "none"
    assert float(body["credit_limit"]) == 0
    assert body["next_tier_in_days"] == 30
    assert body["zero_days"] == []


async def test_streak_counts_consecutive_recorded_days_ending_today(client):
    tokens = await signup(client, "Shop B", "Owner B", "income-b@example.com")
    headers = auth_headers(tokens)

    await _close(client, headers, _today())
    await _close(client, headers, _today() - timedelta(days=1))

    resp = await client.get("/api/v1/turbo/income-profile", headers=headers)
    assert resp.json()["streak_days"] == 2


async def test_streak_breaks_at_first_gap_going_backward(client):
    tokens = await signup(client, "Shop C", "Owner C", "income-c@example.com")
    headers = auth_headers(tokens)

    await _close(client, headers, _today())
    # Yesterday is intentionally left unrecorded — only 2 days ago is closed.
    await _close(client, headers, _today() - timedelta(days=2))

    resp = await client.get("/api/v1/turbo/income-profile", headers=headers)
    assert resp.json()["streak_days"] == 1


async def test_avg_revenue_and_verified_cash_split(client, engine):
    tokens = await signup(client, "Shop D", "Owner D", "income-d@example.com")
    headers = auth_headers(tokens)
    me = await client.get("/api/v1/auth/me", headers=headers)
    tenant_id, user_id = me.json()["tenant_id"], me.json()["id"]

    today_local = datetime.now(_TZ).replace(hour=12, minute=0, second=0, microsecond=0)
    session_factory = async_sessionmaker(engine)
    async with session_factory() as session:
        session.add_all(
            [
                Sale(
                    tenant_id=uuid.UUID(tenant_id),
                    receipt_no="R000001",
                    user_id=uuid.UUID(user_id),
                    client_uuid=uuid.uuid4(),
                    subtotal="100.00",
                    discount="0",
                    total="100.00",
                    payment_method=PaymentMethod.cash,
                    status=SaleStatus.completed,
                    created_at=today_local,
                ),
                Sale(
                    tenant_id=uuid.UUID(tenant_id),
                    receipt_no="R000002",
                    user_id=uuid.UUID(user_id),
                    client_uuid=uuid.uuid4(),
                    subtotal="300.00",
                    discount="0",
                    total="300.00",
                    payment_method=PaymentMethod.transfer_qr,
                    status=SaleStatus.completed,
                    created_at=today_local,
                ),
            ]
        )
        await session.commit()

    resp = await client.get("/api/v1/turbo/income-profile", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["days_recorded"] == 1
    assert float(body["avg_daily_revenue"]) == 400.0
    assert float(body["verified_avg_daily_revenue"]) == 300.0
    assert float(body["cash_avg_daily_revenue"]) == 100.0
    assert float(body["verified_ratio"]) == 0.75
    # credit-weighted = verified (300, full weight) + cash (100 * 0.5) = 350
    assert float(body["credit_weighted_avg_daily_revenue"]) == 350.0


async def test_zero_days_lists_closed_days_with_no_revenue(client):
    tokens = await signup(client, "Shop E", "Owner E", "income-e@example.com")
    headers = auth_headers(tokens)

    await _close(client, headers, _today(), reason="sick")

    resp = await client.get("/api/v1/turbo/income-profile", headers=headers)
    body = resp.json()
    assert body["zero_days"] == [str(_today())]


async def test_credit_tier_unlocks_after_30_day_streak(client):
    tokens = await signup(client, "Shop F", "Owner F", "income-f@example.com")
    headers = auth_headers(tokens)

    for offset in range(30):
        await _close(client, headers, _today() - timedelta(days=offset))

    resp = await client.get("/api/v1/turbo/income-profile", headers=headers)
    body = resp.json()
    assert body["streak_days"] == 30
    assert body["credit_tier"] == "tier_1"
    assert float(body["credit_limit"]) == 10000.0
    assert body["next_tier_in_days"] is None


async def test_cashier_cannot_view_income_profile(client):
    tokens = await signup(client, "Shop G", "Owner G", "income-g@example.com")
    owner_headers = auth_headers(tokens)
    me = await client.get("/api/v1/auth/me", headers=owner_headers)
    tenant_id = me.json()["tenant_id"]

    staff_resp = await client.post(
        "/api/v1/staff", json={"name": "Cashier G", "role": "cashier", "pin": "1234"}, headers=owner_headers
    )
    assert staff_resp.status_code == 201, staff_resp.text
    pin_login = await client.post("/api/v1/auth/pin-login", json={"tenant_id": tenant_id, "pin": "1234"})
    assert pin_login.status_code == 200, pin_login.text
    cashier_headers = auth_headers(pin_login.json())

    resp = await client.get("/api/v1/turbo/income-profile", headers=cashier_headers)
    assert resp.status_code == 403
