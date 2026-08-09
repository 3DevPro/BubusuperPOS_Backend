import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.sale import PaymentMethod, Sale, SaleStatus

from .conftest import auth_headers, signup

_BANGKOK = ZoneInfo("Asia/Bangkok")


async def _insert_sale(engine, tenant_id, user_id, created_at, total="100.00", receipt_no=None):
    session_factory = async_sessionmaker(engine)
    async with session_factory() as session:
        session.add(
            Sale(
                tenant_id=uuid.UUID(tenant_id),
                receipt_no=receipt_no or f"R{uuid.uuid4().hex[:6]}",
                user_id=uuid.UUID(user_id),
                client_uuid=uuid.uuid4(),
                subtotal=total,
                discount="0",
                total=total,
                payment_method=PaymentMethod.cash,
                status=SaleStatus.completed,
                created_at=created_at,
            )
        )
        await session.commit()


async def test_custom_period_summary_only_counts_sales_inside_range(client, engine):
    tokens = await signup(client, "Shop A", "Owner A", "daterange-a@example.com")
    headers = auth_headers(tokens)
    me = await client.get("/api/v1/auth/me", headers=headers)
    tenant_id, user_id = me.json()["tenant_id"], me.json()["id"]

    today_local = datetime.now(_BANGKOK).replace(hour=12, minute=0, second=0, microsecond=0)
    inside = today_local - timedelta(days=2)
    before_range = today_local - timedelta(days=5)
    after_range = today_local + timedelta(days=1)

    await _insert_sale(engine, tenant_id, user_id, inside, total="150.00")
    await _insert_sale(engine, tenant_id, user_id, before_range, total="999.00")
    await _insert_sale(engine, tenant_id, user_id, after_range, total="999.00")

    start_date = (today_local - timedelta(days=3)).date().isoformat()
    end_date = (today_local - timedelta(days=1)).date().isoformat()

    resp = await client.get(
        "/api/v1/reports/summary",
        params={"period": "custom", "start_date": start_date, "end_date": end_date},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sale_count"] == 1
    assert body["revenue"] == "150.00"


async def test_custom_period_requires_both_dates(client):
    tokens = await signup(client, "Shop B", "Owner B", "daterange-b@example.com")
    headers = auth_headers(tokens)

    resp = await client.get(
        "/api/v1/reports/summary", params={"period": "custom", "start_date": "2026-01-01"}, headers=headers
    )
    assert resp.status_code == 400


async def test_custom_period_start_after_end_is_rejected(client):
    tokens = await signup(client, "Shop C", "Owner C", "daterange-c@example.com")
    headers = auth_headers(tokens)

    resp = await client.get(
        "/api/v1/reports/summary",
        params={"period": "custom", "start_date": "2026-02-01", "end_date": "2026-01-01"},
        headers=headers,
    )
    assert resp.status_code == 400


async def test_custom_period_best_sellers(client, engine):
    tokens = await signup(client, "Shop D", "Owner D", "daterange-d@example.com")
    headers = auth_headers(tokens)
    me = await client.get("/api/v1/auth/me", headers=headers)
    tenant_id, user_id = me.json()["tenant_id"], me.json()["id"]

    today_local = datetime.now(_BANGKOK).replace(hour=12, minute=0, second=0, microsecond=0)
    inside = today_local - timedelta(days=1)
    await _insert_sale(engine, tenant_id, user_id, inside)

    start_date = (today_local - timedelta(days=2)).date().isoformat()
    end_date = today_local.date().isoformat()
    resp = await client.get(
        "/api/v1/reports/best-sellers",
        params={"period": "custom", "start_date": start_date, "end_date": end_date},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


async def test_sales_list_filtered_by_date_range(client, engine):
    tokens = await signup(client, "Shop E", "Owner E", "daterange-e@example.com")
    headers = auth_headers(tokens)
    me = await client.get("/api/v1/auth/me", headers=headers)
    tenant_id, user_id = me.json()["tenant_id"], me.json()["id"]

    today_local = datetime.now(_BANGKOK).replace(hour=12, minute=0, second=0, microsecond=0)
    inside = today_local - timedelta(days=2)
    outside = today_local - timedelta(days=10)

    await _insert_sale(engine, tenant_id, user_id, inside, receipt_no="RINSIDE")
    await _insert_sale(engine, tenant_id, user_id, outside, receipt_no="ROUTSIDE")

    start_date = (today_local - timedelta(days=3)).date().isoformat()
    end_date = (today_local - timedelta(days=1)).date().isoformat()
    resp = await client.get(
        "/api/v1/sales", params={"start_date": start_date, "end_date": end_date}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    receipt_nos = [s["receipt_no"] for s in resp.json()]
    assert receipt_nos == ["RINSIDE"]


async def test_sales_list_date_filter_requires_both_dates(client):
    tokens = await signup(client, "Shop F", "Owner F", "daterange-f@example.com")
    headers = auth_headers(tokens)

    resp = await client.get("/api/v1/sales", params={"start_date": "2026-01-01"}, headers=headers)
    assert resp.status_code == 400


async def test_sales_list_without_date_params_returns_everything(client):
    tokens = await signup(client, "Shop G", "Owner G", "daterange-g@example.com")
    headers = auth_headers(tokens)

    resp = await client.get("/api/v1/sales", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == []
