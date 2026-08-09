import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.tenancy import TenantContext
from app.models.sale import PaymentMethod, Sale, SaleStatus
from app.models.user import UserRole
from app.services import report_service

from .conftest import auth_headers, create_product, signup


async def test_report_summary_matches_actual_sales(client):
    tokens = await signup(client, "Shop A", "Owner A", "report-a@example.com")
    headers = auth_headers(tokens)
    product = await create_product(
        client, headers, "กาแฟ", sell_price="45.00", cost_price="20.00", stock_qty=100
    )

    for _ in range(2):
        resp = await client.post(
            "/api/v1/sales",
            json={"client_uuid": str(uuid.uuid4()), "items": [{"product_id": product["id"], "qty": 1}]},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text

    resp = await client.get("/api/v1/reports/summary?period=today", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sale_count"] == 2
    assert body["revenue"] == "90.00"
    assert body["profit"] == "50.00"  # (45-20) * 2
    assert body["item_count"] == 2


async def test_today_boundary_uses_tenant_timezone_not_utc(client, engine):
    # A sale at 01:00 Bangkok time is 18:00 UTC the *previous* calendar day.
    # If the report boundary used naive UTC dates instead of the tenant's
    # timezone, this sale would be wrongly excluded from "today".
    tokens = await signup(client, "Shop B", "Owner B", "report-b@example.com")
    headers = auth_headers(tokens)
    me = await client.get("/api/v1/auth/me", headers=headers)
    tenant_id, user_id = me.json()["tenant_id"], me.json()["id"]

    bangkok = ZoneInfo("Asia/Bangkok")
    today_start_local = datetime.now(bangkok).replace(hour=0, minute=0, second=0, microsecond=0)
    early_morning_local = today_start_local + timedelta(hours=1)  # 01:00 today, Bangkok time

    session_factory = async_sessionmaker(engine)
    async with session_factory() as session:
        session.add(
            Sale(
                tenant_id=uuid.UUID(tenant_id),
                receipt_no="R999999",
                user_id=uuid.UUID(user_id),
                client_uuid=uuid.uuid4(),
                subtotal="100.00",
                discount="0",
                total="100.00",
                payment_method=PaymentMethod.cash,
                status=SaleStatus.completed,
                created_at=early_morning_local,
            )
        )
        await session.commit()

    resp = await client.get("/api/v1/reports/summary?period=today", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sale_count"] == 1
    assert body["revenue"] == "100.00"


async def test_daily_series_buckets_by_local_day_and_fills_gaps(client):
    tokens = await signup(client, "Shop C", "Owner C", "report-c@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "ชา", sell_price="40.00", stock_qty=100)

    resp = await client.post(
        "/api/v1/sales",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"product_id": product["id"], "qty": 1}]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text

    resp = await client.get("/api/v1/reports/daily?days=7", headers=headers)
    assert resp.status_code == 200, resp.text
    points = resp.json()

    assert len(points) == 7
    # Days are returned oldest-first, contiguous, ending on today.
    dates = [p["date"] for p in points]
    assert dates == sorted(dates)
    assert sum(p["sale_count"] for p in points) == 1
    assert sum(float(p["revenue"]) for p in points) == 40.0
    # Every other day has no sales and must still appear as a zero point,
    # not be silently dropped — that's what makes this safe to feed a chart.
    assert sum(1 for p in points if p["sale_count"] == 0) == 6


async def test_best_sellers_ranks_by_qty_sold(client, engine):
    tokens = await signup(client, "Shop D", "Owner D", "report-d@example.com")
    headers = auth_headers(tokens)
    me = await client.get("/api/v1/auth/me", headers=headers)
    tenant_id, user_id = me.json()["tenant_id"], me.json()["id"]

    coffee = await create_product(client, headers, "กาแฟ", sell_price="45.00", stock_qty=100)
    tea = await create_product(client, headers, "ชา", sell_price="40.00", stock_qty=100)

    async def sell(product_id, qty):
        resp = await client.post(
            "/api/v1/sales",
            json={"client_uuid": str(uuid.uuid4()), "items": [{"product_id": product_id, "qty": qty}]},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text

    await sell(coffee["id"], 5)
    await sell(tea["id"], 2)

    session_factory = async_sessionmaker(engine)
    async with session_factory() as session:
        ctx = TenantContext(
            db=session, tenant_id=uuid.UUID(tenant_id), user_id=uuid.UUID(user_id), role=UserRole.owner
        )
        best = await report_service.get_best_sellers(ctx, "today")

    assert [b.name for b in best] == ["กาแฟ", "ชา"]
    assert best[0].qty == 5
    assert best[0].revenue == 225  # 45 * 5
    assert best[1].qty == 2


async def test_best_sellers_endpoint_matches_actual_sales(client):
    tokens = await signup(client, "Shop G", "Owner G", "report-g@example.com")
    headers = auth_headers(tokens)
    coffee = await create_product(client, headers, "กาแฟ", sell_price="45.00", stock_qty=100)
    tea = await create_product(client, headers, "ชา", sell_price="40.00", stock_qty=100)

    async def sell(product_id, qty):
        resp = await client.post(
            "/api/v1/sales",
            json={"client_uuid": str(uuid.uuid4()), "items": [{"product_id": product_id, "qty": qty}]},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text

    await sell(coffee["id"], 3)
    await sell(tea["id"], 1)

    resp = await client.get("/api/v1/reports/best-sellers?period=today&limit=5", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [b["name"] for b in body] == ["กาแฟ", "ชา"]
    assert body[0]["qty"] == 3


async def test_partially_refunded_sale_stays_in_report_with_net_revenue(client):
    tokens = await signup(client, "Shop H", "Owner H", "report-h@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "กาแฟ", sell_price="45.00", stock_qty=100)

    resp = await client.post(
        "/api/v1/sales",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"product_id": product["id"], "qty": 3}]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    sale = resp.json()
    sale_item_id = sale["items"][0]["id"]

    resp = await client.post(
        f"/api/v1/sales/{sale['id']}/refunds",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"sale_item_id": sale_item_id, "qty": 1}]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text

    resp = await client.get("/api/v1/reports/summary?period=today", headers=headers)
    body = resp.json()
    # 3 * 45 = 135 sold, minus 45 refunded = 90 net revenue; the sale itself
    # (partially_refunded) must still count toward sale_count.
    assert body["sale_count"] == 1
    assert body["revenue"] == "90.00"
    assert body["item_count"] == 2  # 3 sold - 1 refunded


async def test_fully_refunded_sale_drops_out_of_report(client):
    tokens = await signup(client, "Shop I", "Owner I", "report-i@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "ชา", sell_price="40.00", stock_qty=100)

    resp = await client.post(
        "/api/v1/sales",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"product_id": product["id"], "qty": 1}]},
        headers=headers,
    )
    sale = resp.json()

    resp = await client.post(
        f"/api/v1/sales/{sale['id']}/refunds",
        json={"client_uuid": str(uuid.uuid4())},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text

    resp = await client.get("/api/v1/reports/summary?period=today", headers=headers)
    body = resp.json()
    assert body["sale_count"] == 0
    assert body["revenue"] == "0"


async def test_worst_sellers_includes_products_with_zero_sales(client):
    tokens = await signup(client, "Shop J", "Owner J", "report-j@example.com")
    headers = auth_headers(tokens)
    coffee = await create_product(client, headers, "กาแฟ", sell_price="45.00", stock_qty=100)
    await create_product(client, headers, "ไม่มีคนซื้อ", sell_price="30.00", stock_qty=100)

    resp = await client.post(
        "/api/v1/sales",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"product_id": coffee["id"], "qty": 5}]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text

    resp = await client.get("/api/v1/reports/worst-sellers?period=today&limit=5", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    names = [b["name"] for b in body]
    assert "ไม่มีคนซื้อ" in names
    zero_seller = next(b for b in body if b["name"] == "ไม่มีคนซื้อ")
    assert zero_seller["qty"] == 0


async def test_sales_by_staff_matches_actual_sales(client):
    tokens = await signup(client, "Shop K", "Owner K", "report-k@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "กาแฟ", sell_price="45.00", stock_qty=10)

    resp = await client.post(
        "/api/v1/sales",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"product_id": product["id"], "qty": 1}]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text

    resp = await client.get("/api/v1/reports/by-staff?period=today", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["sale_count"] == 1
    assert body[0]["revenue"] == "45.00"


async def test_sales_by_payment_method_matches_actual_sales(client):
    tokens = await signup(client, "Shop L", "Owner L", "report-l@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "กาแฟ", sell_price="45.00", stock_qty=10)

    resp = await client.post(
        "/api/v1/sales",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"product_id": product["id"], "qty": 1}]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text

    resp = await client.get("/api/v1/reports/by-payment-method?period=today", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["payment_method"] == "cash"
    assert body[0]["revenue"] == "45.00"


async def test_compare_periods_computes_revenue_diff(client):
    tokens = await signup(client, "Shop M", "Owner M", "report-m@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "กาแฟ", sell_price="45.00", stock_qty=10)

    resp = await client.post(
        "/api/v1/sales",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"product_id": product["id"], "qty": 2}]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text

    resp = await client.get(
        "/api/v1/reports/compare?period_a=today&period_b=yesterday", headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["summary_a"]["revenue"] == "90.00"
    assert body["summary_b"]["revenue"] == "0"
    assert body["revenue_diff"] == "90.00"
    assert body["revenue_diff_pct"] is None  # summary_b.revenue is 0 — pct undefined
