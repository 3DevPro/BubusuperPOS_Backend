import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.stock import StockMovement

from .conftest import auth_headers, create_product, enable_vat, signup


async def _checkout(client, headers, product_id, qty, discount="0"):
    resp = await client.post(
        "/api/v1/sales",
        json={
            "client_uuid": str(uuid.uuid4()),
            "items": [{"product_id": product_id, "qty": qty}],
            "discount": discount,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _sale_detail(client, headers, sale_id):
    resp = await client.get(f"/api/v1/sales/{sale_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _pin_login(client, headers_owner, tenant_id, role, pin):
    resp = await client.post(
        "/api/v1/staff", json={"name": role, "role": role, "pin": pin}, headers=headers_owner
    )
    assert resp.status_code == 201, resp.text
    login = await client.post("/api/v1/auth/pin-login", json={"tenant_id": tenant_id, "pin": pin})
    assert login.status_code == 200, login.text
    return auth_headers(login.json())


async def test_partial_refund_restocks_and_marks_partially_refunded(client, engine):
    tokens = await signup(client, "Shop A", "Owner A", "refund-a@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "กาแฟ", sell_price="45.00", stock_qty=10)
    sale = await _checkout(client, headers, product["id"], 3)
    sale_item_id = (await _sale_detail(client, headers, sale["id"]))["items"][0]["id"]

    resp = await client.post(
        f"/api/v1/sales/{sale['id']}/refunds",
        json={
            "client_uuid": str(uuid.uuid4()),
            "items": [{"sale_item_id": sale_item_id, "qty": 1}],
            "reason": "ลูกค้าเปลี่ยนใจ",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["sale_status"] == "partially_refunded"
    assert body["refund_amount"] == "45.00"

    resp = await client.get("/api/v1/products", headers=headers)
    assert resp.json()[0]["stock_qty"] == 8  # 10 - 3 + 1

    session_factory = async_sessionmaker(engine)
    async with session_factory() as session:
        movements = (
            await session.scalars(
                select(StockMovement)
                .where(StockMovement.product_id == product["id"])
                .order_by(StockMovement.created_at)
            )
        ).all()
    assert len(movements) == 2
    assert movements[1].type.value == "return"
    assert movements[1].qty_delta == 1

    detail = await _sale_detail(client, headers, sale["id"])
    assert detail["items"][0]["refunded_qty"] == 1
    assert detail["refunded_total"] == "45.00"


async def test_full_refund_marks_refunded_and_restocks_all(client):
    tokens = await signup(client, "Shop B", "Owner B", "refund-b@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "ชา", sell_price="40.00", stock_qty=10)
    sale = await _checkout(client, headers, product["id"], 3)

    resp = await client.post(
        f"/api/v1/sales/{sale['id']}/refunds",
        json={"client_uuid": str(uuid.uuid4())},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["sale_status"] == "refunded"
    assert body["refund_amount"] == "120.00"

    resp = await client.get("/api/v1/products", headers=headers)
    assert resp.json()[0]["stock_qty"] == 10  # fully restocked


async def test_refund_more_than_remaining_qty_is_rejected_and_persists_nothing(client):
    tokens = await signup(client, "Shop C", "Owner C", "refund-c@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "โซดา", sell_price="15.00", stock_qty=10)
    sale = await _checkout(client, headers, product["id"], 2)
    sale_item_id = (await _sale_detail(client, headers, sale["id"]))["items"][0]["id"]

    resp = await client.post(
        f"/api/v1/sales/{sale['id']}/refunds",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"sale_item_id": sale_item_id, "qty": 99}]},
        headers=headers,
    )
    assert resp.status_code == 400

    resp = await client.get("/api/v1/products", headers=headers)
    assert resp.json()[0]["stock_qty"] == 8  # unchanged: 10 - 2, no refund applied

    detail = await _sale_detail(client, headers, sale["id"])
    assert detail["refunds"] == []
    assert detail["status"] == "completed"


async def test_refunding_twice_until_exhausted_then_rejects_further(client):
    tokens = await signup(client, "Shop D", "Owner D", "refund-d@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "ขนม", sell_price="25.00", stock_qty=10)
    sale = await _checkout(client, headers, product["id"], 2)
    sale_item_id = (await _sale_detail(client, headers, sale["id"]))["items"][0]["id"]

    first = await client.post(
        f"/api/v1/sales/{sale['id']}/refunds",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"sale_item_id": sale_item_id, "qty": 1}]},
        headers=headers,
    )
    assert first.status_code == 201, first.text
    assert first.json()["sale_status"] == "partially_refunded"

    second = await client.post(
        f"/api/v1/sales/{sale['id']}/refunds",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"sale_item_id": sale_item_id, "qty": 1}]},
        headers=headers,
    )
    assert second.status_code == 201, second.text
    assert second.json()["sale_status"] == "refunded"

    third = await client.post(
        f"/api/v1/sales/{sale['id']}/refunds",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"sale_item_id": sale_item_id, "qty": 1}]},
        headers=headers,
    )
    assert third.status_code == 400


async def test_void_sale_cannot_be_refunded(client, engine):
    tokens = await signup(client, "Shop E", "Owner E", "refund-e@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "น้ำแข็ง", sell_price="10.00", stock_qty=10)
    sale = await _checkout(client, headers, product["id"], 1)

    from app.models.sale import Sale, SaleStatus

    session_factory = async_sessionmaker(engine)
    async with session_factory() as session:
        row = await session.get(Sale, uuid.UUID(sale["id"]))
        row.status = SaleStatus.void
        await session.commit()

    resp = await client.post(
        f"/api/v1/sales/{sale['id']}/refunds",
        json={"client_uuid": str(uuid.uuid4())},
        headers=headers,
    )
    assert resp.status_code == 400


async def test_refund_idempotent_by_client_uuid(client):
    tokens = await signup(client, "Shop F", "Owner F", "refund-f@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "น้ำอัดลม", sell_price="10.00", stock_qty=10)
    sale = await _checkout(client, headers, product["id"], 2)

    payload = {"client_uuid": str(uuid.uuid4())}
    first = await client.post(f"/api/v1/sales/{sale['id']}/refunds", json=payload, headers=headers)
    second = await client.post(f"/api/v1/sales/{sale['id']}/refunds", json=payload, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]

    resp = await client.get("/api/v1/products", headers=headers)
    assert resp.json()[0]["stock_qty"] == 10  # restocked exactly once, not twice


async def test_cashier_cannot_refund_manager_can(client):
    tokens = await signup(client, "Shop G", "Owner G", "refund-g@example.com")
    headers_owner = auth_headers(tokens)
    me = await client.get("/api/v1/auth/me", headers=headers_owner)
    tenant_id = me.json()["tenant_id"]

    product = await create_product(client, headers_owner, "กาแฟเย็น", sell_price="55.00", stock_qty=10)
    sale = await _checkout(client, headers_owner, product["id"], 1)

    cashier_headers = await _pin_login(client, headers_owner, tenant_id, "cashier", "1111")
    resp = await client.post(
        f"/api/v1/sales/{sale['id']}/refunds",
        json={"client_uuid": str(uuid.uuid4())},
        headers=cashier_headers,
    )
    assert resp.status_code == 403

    manager_headers = await _pin_login(client, headers_owner, tenant_id, "manager", "2222")
    resp = await client.post(
        f"/api/v1/sales/{sale['id']}/refunds",
        json={"client_uuid": str(uuid.uuid4())},
        headers=manager_headers,
    )
    assert resp.status_code == 201, resp.text


async def test_refund_does_not_touch_stock_for_untracked_products(client):
    tokens = await signup(client, "Shop H", "Owner H", "refund-h@example.com")
    headers = auth_headers(tokens)
    product = await create_product(
        client, headers, "บริการ", sell_price="200.00", stock_qty=0, track_stock=False
    )
    sale = await _checkout(client, headers, product["id"], 1)

    resp = await client.post(
        f"/api/v1/sales/{sale['id']}/refunds",
        json={"client_uuid": str(uuid.uuid4())},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text

    resp = await client.get("/api/v1/products", headers=headers)
    assert resp.json()[0]["stock_qty"] == 0


async def test_refund_prorates_sale_discount_and_vat_on_full_refund(client):
    tokens = await signup(client, "Shop I", "Owner I", "refund-i@example.com")
    headers = auth_headers(tokens)
    await enable_vat(client, headers, rate="7.00", price_includes_tax=False)
    product = await create_product(client, headers, "กาแฟ", sell_price="100.00", stock_qty=10)

    resp = await client.post(
        "/api/v1/sales",
        json={
            "client_uuid": str(uuid.uuid4()),
            "items": [{"product_id": product["id"], "qty": 2}],
            "discount": "20.00",
        },
        headers=headers,
    )
    sale = resp.json()
    # subtotal=200, discount=20, net=180, tax=180*7%=12.60, total=192.60

    resp = await client.post(
        f"/api/v1/sales/{sale['id']}/refunds",
        json={"client_uuid": str(uuid.uuid4())},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["sale_status"] == "refunded"
    assert body["refund_amount"] == sale["total"]
    assert body["refund_tax"] == sale["tax"]
