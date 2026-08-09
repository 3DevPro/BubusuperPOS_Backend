from .conftest import auth_headers, create_product, create_supplier, signup


async def _create_po(client, headers, supplier_id, product_id, qty=10, unit_cost="20.00"):
    resp = await client.post(
        "/api/v1/purchase-orders",
        json={
            "supplier_id": supplier_id,
            "items": [{"product_id": product_id, "qty": qty, "unit_cost": unit_cost}],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_create_purchase_order(client):
    tokens = await signup(client, "Shop A", "Owner A", "po-a@example.com")
    headers = auth_headers(tokens)
    supplier = await create_supplier(client, headers, "ซัพพลายเออร์เอ")
    product = await create_product(client, headers, "ข้าวสาร", sell_price="60.00", stock_qty=5)

    po = await _create_po(client, headers, supplier["id"], product["id"], qty=20, unit_cost="35.00")
    assert po["status"] == "ordered"
    assert po["order_no"].startswith("PO")
    assert po["items"][0]["qty_ordered"] == 20
    assert po["items"][0]["qty_received"] == 0

    # Stock must not move until something is actually received.
    products = await client.get("/api/v1/products", headers=headers)
    p = next(p for p in products.json() if p["id"] == product["id"])
    assert p["stock_qty"] == 5


async def test_full_receive_marks_received_and_increments_stock(client):
    tokens = await signup(client, "Shop B", "Owner B", "po-b@example.com")
    headers = auth_headers(tokens)
    supplier = await create_supplier(client, headers, "ซัพพลายเออร์บี")
    product = await create_product(client, headers, "น้ำตาล", sell_price="30.00", stock_qty=0)
    po = await _create_po(client, headers, supplier["id"], product["id"], qty=15, unit_cost="18.00")
    poi_id = po["items"][0]["id"]

    resp = await client.post(
        f"/api/v1/purchase-orders/{po['id']}/receive",
        json={"items": [{"purchase_order_item_id": poi_id, "qty": 15}]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "received"
    assert resp.json()["items"][0]["qty_received"] == 15

    products = await client.get("/api/v1/products", headers=headers)
    p = next(p for p in products.json() if p["id"] == product["id"])
    assert p["stock_qty"] == 15

    audit = await client.get("/api/v1/audit-log", headers=headers)
    assert any(a["action"] == "purchase_order.receive" for a in audit.json())


async def test_partial_receive_marks_partially_received(client):
    tokens = await signup(client, "Shop C", "Owner C", "po-c@example.com")
    headers = auth_headers(tokens)
    supplier = await create_supplier(client, headers, "ซัพพลายเออร์ซี")
    product = await create_product(client, headers, "แป้งสาลี", sell_price="40.00", stock_qty=0)
    po = await _create_po(client, headers, supplier["id"], product["id"], qty=100, unit_cost="10.00")
    poi_id = po["items"][0]["id"]

    first = await client.post(
        f"/api/v1/purchase-orders/{po['id']}/receive",
        json={"items": [{"purchase_order_item_id": poi_id, "qty": 60}]},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "partially_received"

    products = await client.get("/api/v1/products", headers=headers)
    assert next(p for p in products.json() if p["id"] == product["id"])["stock_qty"] == 60

    second = await client.post(
        f"/api/v1/purchase-orders/{po['id']}/receive",
        json={"items": [{"purchase_order_item_id": poi_id, "qty": 40}]},
        headers=headers,
    )
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "received"

    products = await client.get("/api/v1/products", headers=headers)
    assert next(p for p in products.json() if p["id"] == product["id"])["stock_qty"] == 100


async def test_receive_more_than_ordered_is_rejected_and_persists_nothing(client):
    tokens = await signup(client, "Shop D", "Owner D", "po-d@example.com")
    headers = auth_headers(tokens)
    supplier = await create_supplier(client, headers, "ซัพพลายเออร์ดี")
    product = await create_product(client, headers, "เกลือ", sell_price="15.00", stock_qty=0)
    po = await _create_po(client, headers, supplier["id"], product["id"], qty=10, unit_cost="5.00")
    poi_id = po["items"][0]["id"]

    resp = await client.post(
        f"/api/v1/purchase-orders/{po['id']}/receive",
        json={"items": [{"purchase_order_item_id": poi_id, "qty": 11}]},
        headers=headers,
    )
    assert resp.status_code == 400

    products = await client.get("/api/v1/products", headers=headers)
    assert next(p for p in products.json() if p["id"] == product["id"])["stock_qty"] == 0


async def test_receiving_twice_until_exhausted_then_rejects_further(client):
    tokens = await signup(client, "Shop E", "Owner E", "po-e@example.com")
    headers = auth_headers(tokens)
    supplier = await create_supplier(client, headers, "ซัพพลายเออร์อี")
    product = await create_product(client, headers, "พริกไทย", sell_price="25.00", stock_qty=0)
    po = await _create_po(client, headers, supplier["id"], product["id"], qty=5, unit_cost="8.00")
    poi_id = po["items"][0]["id"]

    await client.post(
        f"/api/v1/purchase-orders/{po['id']}/receive",
        json={"items": [{"purchase_order_item_id": poi_id, "qty": 5}]},
        headers=headers,
    )
    resp = await client.post(
        f"/api/v1/purchase-orders/{po['id']}/receive",
        json={"items": [{"purchase_order_item_id": poi_id, "qty": 1}]},
        headers=headers,
    )
    assert resp.status_code == 400


async def test_cancel_before_any_receiving(client):
    tokens = await signup(client, "Shop F", "Owner F", "po-f@example.com")
    headers = auth_headers(tokens)
    supplier = await create_supplier(client, headers, "ซัพพลายเออร์เอฟ")
    product = await create_product(client, headers, "ซอส", sell_price="45.00", stock_qty=0)
    po = await _create_po(client, headers, supplier["id"], product["id"])

    resp = await client.post(f"/api/v1/purchase-orders/{po['id']}/cancel", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"

    # A cancelled order can no longer be received.
    poi_id = po["items"][0]["id"]
    receive = await client.post(
        f"/api/v1/purchase-orders/{po['id']}/receive",
        json={"items": [{"purchase_order_item_id": poi_id, "qty": 1}]},
        headers=headers,
    )
    assert receive.status_code == 400


async def test_cannot_cancel_after_partial_receive(client):
    tokens = await signup(client, "Shop G", "Owner G", "po-g@example.com")
    headers = auth_headers(tokens)
    supplier = await create_supplier(client, headers, "ซัพพลายเออร์จี")
    product = await create_product(client, headers, "นม", sell_price="22.00", stock_qty=0)
    po = await _create_po(client, headers, supplier["id"], product["id"], qty=10)
    poi_id = po["items"][0]["id"]

    await client.post(
        f"/api/v1/purchase-orders/{po['id']}/receive",
        json={"items": [{"purchase_order_item_id": poi_id, "qty": 3}]},
        headers=headers,
    )
    resp = await client.post(f"/api/v1/purchase-orders/{po['id']}/cancel", headers=headers)
    assert resp.status_code == 400


async def test_receive_does_not_touch_stock_for_untracked_products(client):
    tokens = await signup(client, "Shop H", "Owner H", "po-h@example.com")
    headers = auth_headers(tokens)
    supplier = await create_supplier(client, headers, "ซัพพลายเออร์เอช")
    product = await create_product(client, headers, "บริการ", sell_price="0", stock_qty=0, track_stock=False)
    po = await _create_po(client, headers, supplier["id"], product["id"], qty=5)
    poi_id = po["items"][0]["id"]

    resp = await client.post(
        f"/api/v1/purchase-orders/{po['id']}/receive",
        json={"items": [{"purchase_order_item_id": poi_id, "qty": 5}]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    products = await client.get("/api/v1/products", headers=headers)
    assert next(p for p in products.json() if p["id"] == product["id"])["stock_qty"] == 0


async def test_cashier_denied_purchase_order_access(client):
    tokens = await signup(client, "Shop I", "Owner I", "po-i@example.com")
    headers = auth_headers(tokens)
    supplier = await create_supplier(client, headers, "ซัพพลายเออร์ไอ")
    product = await create_product(client, headers, "ชาไทย", sell_price="20.00", stock_qty=0)

    cashier_resp = await client.post(
        "/api/v1/staff", json={"name": "Cashier", "role": "cashier", "pin": "1234"}, headers=headers
    )
    assert cashier_resp.status_code == 201, cashier_resp.text
    me = await client.get("/api/v1/auth/me", headers=headers)
    tenant_id = me.json()["tenant_id"]
    login = await client.post("/api/v1/auth/pin-login", json={"tenant_id": tenant_id, "pin": "1234"})
    cashier_headers = auth_headers(login.json())

    resp = await client.post(
        "/api/v1/purchase-orders",
        json={"supplier_id": supplier["id"], "items": [{"product_id": product["id"], "qty": 1, "unit_cost": "1"}]},
        headers=cashier_headers,
    )
    assert resp.status_code == 403


async def test_purchase_order_does_not_leak_across_tenants(client):
    tokens_a = await signup(client, "Shop J", "Owner J", "po-j@example.com")
    headers_a = auth_headers(tokens_a)
    supplier_a = await create_supplier(client, headers_a, "ซัพพลายเออร์เจ")
    product_a = await create_product(client, headers_a, "กาแฟดำ", sell_price="30.00", stock_qty=0)
    po = await _create_po(client, headers_a, supplier_a["id"], product_a["id"])

    tokens_b = await signup(client, "Shop K", "Owner K", "po-k@example.com")
    resp = await client.get(f"/api/v1/purchase-orders/{po['id']}", headers=auth_headers(tokens_b))
    assert resp.status_code == 404
