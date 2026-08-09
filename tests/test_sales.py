import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.stock import StockMovement

from .conftest import auth_headers, create_product, enable_vat, signup


async def test_sale_decrements_stock_and_writes_ledger(client, engine):
    tokens = await signup(client, "Shop A", "Owner A", "sale-a@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "กาแฟ", sell_price="45.00", stock_qty=10)

    resp = await client.post(
        "/api/v1/sales",
        json={
            "client_uuid": str(uuid.uuid4()),
            "items": [{"product_id": product["id"], "qty": 3}],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["warnings"] == []

    resp = await client.get("/api/v1/products", headers=headers)
    assert resp.json()[0]["stock_qty"] == 7

    session_factory = async_sessionmaker(engine)
    async with session_factory() as session:
        movements = (
            await session.scalars(select(StockMovement).where(StockMovement.product_id == product["id"]))
        ).all()
    assert len(movements) == 1
    assert movements[0].qty_delta == -3
    assert movements[0].type.value == "sale"


async def test_duplicate_client_uuid_is_idempotent(client):
    tokens = await signup(client, "Shop B", "Owner B", "sale-b@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "ชา", sell_price="40.00", stock_qty=10)

    client_uuid = str(uuid.uuid4())
    payload = {"client_uuid": client_uuid, "items": [{"product_id": product["id"], "qty": 2}]}

    first = await client.post("/api/v1/sales", json=payload, headers=headers)
    second = await client.post("/api/v1/sales", json=payload, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["receipt_no"] == second.json()["receipt_no"]

    resp = await client.get("/api/v1/sales", headers=headers)
    assert len(resp.json()) == 1


async def test_price_is_taken_from_catalog_not_client(client):
    tokens = await signup(client, "Shop C", "Owner C", "sale-c@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "ขนม", sell_price="25.00", stock_qty=10)

    resp = await client.post(
        "/api/v1/sales",
        json={
            "client_uuid": str(uuid.uuid4()),
            # The request schema has no price field at all — even smuggling one
            # into the item payload must have zero effect on what's charged.
            "items": [{"product_id": product["id"], "qty": 2, "price": "0.01"}],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["subtotal"] == "50.00"
    assert body["items"][0]["price"] == "25.00"


async def test_oversell_succeeds_with_warning(client):
    tokens = await signup(client, "Shop D", "Owner D", "sale-d@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "น้ำแข็ง", sell_price="10.00", stock_qty=3)

    resp = await client.post(
        "/api/v1/sales",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"product_id": product["id"], "qty": 5}]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert len(resp.json()["warnings"]) == 1

    resp = await client.get("/api/v1/products", headers=headers)
    assert resp.json()[0]["stock_qty"] == -2


async def test_cross_tenant_sale_lookup_is_404(client):
    tokens_a = await signup(client, "Shop E", "Owner E", "sale-e@example.com")
    tokens_b = await signup(client, "Shop F", "Owner F", "sale-f@example.com")
    headers_a, headers_b = auth_headers(tokens_a), auth_headers(tokens_b)

    product = await create_product(client, headers_a, "โซดา", sell_price="15.00", stock_qty=10)
    sale = await client.post(
        "/api/v1/sales",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"product_id": product["id"], "qty": 1}]},
        headers=headers_a,
    )
    sale_id = sale.json()["id"]

    resp = await client.get(f"/api/v1/sales/{sale_id}", headers=headers_b)
    assert resp.status_code == 404


async def test_receipt_numbers_are_unique_per_tenant(client):
    tokens = await signup(client, "Shop G", "Owner G", "sale-g@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "เค้ก", sell_price="35.00", stock_qty=100)

    receipts = []
    for _ in range(5):
        resp = await client.post(
            "/api/v1/sales",
            json={"client_uuid": str(uuid.uuid4()), "items": [{"product_id": product["id"], "qty": 1}]},
            headers=headers,
        )
        receipts.append(resp.json()["receipt_no"])

    assert len(set(receipts)) == 5


async def test_per_item_discount_reduces_line_total(client):
    tokens = await signup(client, "Shop H", "Owner H", "sale-h@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "กาแฟ", sell_price="45.00", stock_qty=10)

    resp = await client.post(
        "/api/v1/sales",
        json={
            "client_uuid": str(uuid.uuid4()),
            "items": [{"product_id": product["id"], "qty": 2, "discount": "10.00"}],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # (45*2) - 10 = 80
    assert body["items"][0]["line_total"] == "80.00"
    assert body["subtotal"] == "80.00"
    assert body["total"] == "80.00"


async def test_per_item_discount_exceeding_line_total_is_rejected(client):
    tokens = await signup(client, "Shop I", "Owner I", "sale-i@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "ชา", sell_price="40.00", stock_qty=10)

    resp = await client.post(
        "/api/v1/sales",
        json={
            "client_uuid": str(uuid.uuid4()),
            "items": [{"product_id": product["id"], "qty": 1, "discount": "999.00"}],
        },
        headers=headers,
    )
    assert resp.status_code == 400


async def test_vat_disabled_by_default_leaves_totals_unchanged(client):
    # Regression guard: a tenant that never touches VAT settings must see
    # byte-for-byte the same totals as before VAT existed.
    tokens = await signup(client, "Shop J", "Owner J", "sale-j@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "น้ำเปล่า", sell_price="20.00", stock_qty=10)

    resp = await client.post(
        "/api/v1/sales",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"product_id": product["id"], "qty": 3}]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["tax"] == "0.00" or body["tax"] == "0"
    assert body["subtotal"] == "60.00"
    assert body["total"] == "60.00"


async def test_vat_inclusive_backs_out_tax_without_changing_total(client):
    tokens = await signup(client, "Shop K", "Owner K", "sale-k@example.com")
    headers = auth_headers(tokens)
    await enable_vat(client, headers, rate="7.00", price_includes_tax=True)
    product = await create_product(client, headers, "กาแฟ", sell_price="107.00", stock_qty=10)

    resp = await client.post(
        "/api/v1/sales",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"product_id": product["id"], "qty": 1}]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # 107 already includes 7% VAT -> tax = 107 * 7/107 = 7.00, total stays 107
    assert body["total"] == "107.00"
    assert body["tax"] == "7.00"


async def test_vat_exclusive_adds_tax_on_top_of_total(client):
    tokens = await signup(client, "Shop L", "Owner L", "sale-l@example.com")
    headers = auth_headers(tokens)
    await enable_vat(client, headers, rate="7.00", price_includes_tax=False)
    product = await create_product(client, headers, "กาแฟ", sell_price="100.00", stock_qty=10)

    resp = await client.post(
        "/api/v1/sales",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"product_id": product["id"], "qty": 1}]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["tax"] == "7.00"
    assert body["total"] == "107.00"


async def test_sale_level_discount_reduces_taxable_base_before_vat(client):
    tokens = await signup(client, "Shop M", "Owner M", "sale-m@example.com")
    headers = auth_headers(tokens)
    await enable_vat(client, headers, rate="7.00", price_includes_tax=False)
    product = await create_product(client, headers, "กาแฟ", sell_price="100.00", stock_qty=10)

    resp = await client.post(
        "/api/v1/sales",
        json={
            "client_uuid": str(uuid.uuid4()),
            "items": [{"product_id": product["id"], "qty": 1}],
            "discount": "20.00",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # net after sale-level discount = 100 - 20 = 80; tax = 80 * 7% = 5.60
    assert body["tax"] == "5.60"
    assert body["total"] == "85.60"


async def test_get_sale_by_receipt_no_case_insensitive(client):
    tokens = await signup(client, "Shop N", "Owner N", "sale-n@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "กาแฟ", sell_price="45.00", stock_qty=10)

    resp = await client.post(
        "/api/v1/sales",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"product_id": product["id"], "qty": 1}]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    sale = resp.json()

    resp = await client.get(f"/api/v1/sales/by-receipt/{sale['receipt_no'].lower()}", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == sale["id"]


async def test_get_sale_by_receipt_no_404_when_missing(client):
    tokens = await signup(client, "Shop O", "Owner O", "sale-o@example.com")
    headers = auth_headers(tokens)

    resp = await client.get("/api/v1/sales/by-receipt/NOPE-0001", headers=headers)
    assert resp.status_code == 404
