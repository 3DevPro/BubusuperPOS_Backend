import uuid

from .conftest import auth_headers, create_product, signup


async def _pin_login(client, headers_owner, tenant_id, role, pin):
    resp = await client.post("/api/v1/staff", json={"name": role, "role": role, "pin": pin}, headers=headers_owner)
    assert resp.status_code == 201, resp.text
    login = await client.post("/api/v1/auth/pin-login", json={"tenant_id": tenant_id, "pin": pin})
    assert login.status_code == 200, login.text
    return auth_headers(login.json())


async def test_create_promotion_requires_manage_promotions(client):
    tokens = await signup(client, "Promo Shop A", "Owner", "promo-a@example.com")
    headers_owner = auth_headers(tokens)
    me = await client.get("/api/v1/auth/me", headers=headers_owner)
    tenant_id = me.json()["tenant_id"]
    headers_cashier = await _pin_login(client, headers_owner, tenant_id, "cashier", "1234")

    resp = await client.post(
        "/api/v1/promotions",
        json={"name": "ลด 10%", "kind": "percent_off", "scope": "all", "percent_off": "10"},
        headers=headers_cashier,
    )
    assert resp.status_code == 403


async def test_crud_promotion_lifecycle(client):
    tokens = await signup(client, "Promo Shop B", "Owner", "promo-b@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "สินค้า", sell_price="100.00", stock_qty=10)

    create = await client.post(
        "/api/v1/promotions",
        json={
            "name": "ลด 10% กาแฟ",
            "kind": "percent_off",
            "scope": "product",
            "percent_off": "10",
            "target_product_ids": [product["id"]],
        },
        headers=headers,
    )
    assert create.status_code == 201, create.text
    promo = create.json()
    assert promo["target_product_ids"] == [product["id"]]
    assert promo["is_active"] is True

    listed = await client.get("/api/v1/promotions", headers=headers)
    assert len(listed.json()) == 1

    updated = await client.patch(
        f"/api/v1/promotions/{promo['id']}", json={"percent_off": "15"}, headers=headers
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["percent_off"] == "15.00"

    deactivated = await client.delete(f"/api/v1/promotions/{promo['id']}", headers=headers)
    assert deactivated.status_code == 204

    active = await client.get("/api/v1/promotions/active", headers=headers)
    assert active.json() == []


async def test_percent_off_applies_at_checkout(client):
    tokens = await signup(client, "Promo Shop C", "Owner", "promo-c@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "สินค้า", sell_price="100.00", stock_qty=10)
    await client.post(
        "/api/v1/promotions",
        json={"name": "ลด 10%", "kind": "percent_off", "scope": "all", "percent_off": "10"},
        headers=headers,
    )

    resp = await client.post(
        "/api/v1/sales",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"product_id": product["id"], "qty": 1}]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["items"][0]["promo_discount"] == "10.00"
    assert body["subtotal"] == "90.00"
    assert body["total"] == "90.00"
    assert len(body["applied_promotions"]) == 1
    assert body["applied_promotions"][0]["discount_amount"] == "10.00"


async def test_buy_n_get_m_free_gift_decrements_stock_and_splits_receipt_line(client):
    tokens = await signup(client, "Promo Shop D", "Owner", "promo-d@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "น้ำอัดลม", sell_price="20.00", cost_price="8.00", stock_qty=10)
    await client.post(
        "/api/v1/promotions",
        json={
            "name": "ซื้อ 2 แถม 1",
            "kind": "buy_n_get_m",
            "scope": "all",
            "buy_qty": 2,
            "get_qty": 1,
        },
        headers=headers,
    )

    resp = await client.post(
        "/api/v1/sales",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"product_id": product["id"], "qty": 3}]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert len(body["items"]) == 2
    paid = next(i for i in body["items"] if not i["is_free_gift"])
    free = next(i for i in body["items"] if i["is_free_gift"])
    assert paid["qty"] == 2
    assert paid["line_total"] == "40.00"
    assert free["qty"] == 1
    assert free["line_total"] == "0.00"
    assert body["subtotal"] == "40.00"

    # All 3 physical units left the shelf, not just the 2 paid ones — this
    # is the stock-decrement-loop bug the promo engine wiring fixed.
    product_after = await client.get(f"/api/v1/products/{product['id']}", headers=headers)
    assert product_after.json()["stock_qty"] == 7


async def test_free_gift_line_reports_negative_margin_not_full_margin(client):
    tokens = await signup(client, "Promo Shop E", "Owner", "promo-e@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "น้ำอัดลม", sell_price="20.00", cost_price="8.00", stock_qty=10)
    await client.post(
        "/api/v1/promotions",
        json={"name": "ซื้อ 2 แถม 1", "kind": "buy_n_get_m", "scope": "all", "buy_qty": 2, "get_qty": 1},
        headers=headers,
    )
    resp = await client.post(
        "/api/v1/sales",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"product_id": product["id"], "qty": 3}]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text

    summary = await client.get("/api/v1/reports/summary?period=today", headers=headers)
    assert summary.status_code == 200, summary.text
    # revenue 40 (2 paid units), cost 8*3=24 (all 3 units cost real money) -> profit 16
    assert summary.json()["revenue"] == "40.00"
    assert summary.json()["profit"] == "16.00"


async def test_refund_on_free_gift_sale_returns_stock_and_zero_refund_for_gift(client):
    tokens = await signup(client, "Promo Shop F", "Owner", "promo-f@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "น้ำอัดลม", sell_price="20.00", cost_price="8.00", stock_qty=10)
    await client.post(
        "/api/v1/promotions",
        json={"name": "ซื้อ 2 แถม 1", "kind": "buy_n_get_m", "scope": "all", "buy_qty": 2, "get_qty": 1},
        headers=headers,
    )
    sale = (
        await client.post(
            "/api/v1/sales",
            json={"client_uuid": str(uuid.uuid4()), "items": [{"product_id": product["id"], "qty": 3}]},
            headers=headers,
        )
    ).json()
    free_item = next(i for i in sale["items"] if i["is_free_gift"])

    refund = await client.post(
        f"/api/v1/sales/{sale['id']}/refunds",
        json={
            "client_uuid": str(uuid.uuid4()),
            "items": [{"sale_item_id": free_item["id"], "qty": 1}],
            "reason": "คืนของแถม",
        },
        headers=headers,
    )
    assert refund.status_code == 201, refund.text
    assert refund.json()["refund_amount"] == "0.00"

    product_after = await client.get(f"/api/v1/products/{product['id']}", headers=headers)
    assert product_after.json()["stock_qty"] == 8  # 10 - 3 + 1 returned


async def test_buy_n_for_price_bundles_correctly_at_checkout(client):
    tokens = await signup(client, "Promo Shop G", "Owner", "promo-g@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "สินค้า", sell_price="50.00", stock_qty=10)
    await client.post(
        "/api/v1/promotions",
        json={"name": "ซื้อ 3 ราคา 120", "kind": "buy_n_for_price", "scope": "all", "buy_qty": 3, "bundle_price": "120.00"},
        headers=headers,
    )
    resp = await client.post(
        "/api/v1/sales",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"product_id": product["id"], "qty": 4}]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["subtotal"] == "170.00"  # 120 bundled + 50 remainder


async def test_coupon_code_applies_discount(client):
    tokens = await signup(client, "Promo Shop H", "Owner", "promo-h@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "สินค้า", sell_price="100.00", stock_qty=10)
    await client.post(
        "/api/v1/promotions",
        json={"name": "คูปองลด 20", "kind": "amount_off", "scope": "all", "amount_off": "20.00", "coupon_code": "SAVE20"},
        headers=headers,
    )

    resp = await client.post(
        "/api/v1/sales",
        json={
            "client_uuid": str(uuid.uuid4()),
            "items": [{"product_id": product["id"], "qty": 1}],
            "coupon_code": "save20",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["subtotal"] == "80.00"


async def test_invalid_coupon_code_rejected(client):
    tokens = await signup(client, "Promo Shop I", "Owner", "promo-i@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "สินค้า", sell_price="100.00", stock_qty=10)

    resp = await client.post(
        "/api/v1/sales",
        json={
            "client_uuid": str(uuid.uuid4()),
            "items": [{"product_id": product["id"], "qty": 1}],
            "coupon_code": "NOTREAL",
        },
        headers=headers,
    )
    assert resp.status_code == 400


async def test_preview_matches_actual_checkout_totals(client):
    tokens = await signup(client, "Promo Shop J", "Owner", "promo-j@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "สินค้า", sell_price="100.00", stock_qty=10)
    await client.post(
        "/api/v1/promotions",
        json={"name": "ลด 10%", "kind": "percent_off", "scope": "all", "percent_off": "10"},
        headers=headers,
    )

    preview = await client.post(
        "/api/v1/sales/preview",
        json={"items": [{"product_id": product["id"], "qty": 2}]},
        headers=headers,
    )
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()

    sale = await client.post(
        "/api/v1/sales",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"product_id": product["id"], "qty": 2}]},
        headers=headers,
    )
    assert sale.status_code == 201, sale.text
    sale_body = sale.json()

    assert preview_body["subtotal"] == sale_body["subtotal"]
    assert preview_body["total"] == sale_body["total"]
    assert preview_body["items"][0]["promo_discount"] == sale_body["items"][0]["promo_discount"]

    # Preview never writes anything — stock is untouched by the preview
    # call itself (only by the real checkout that followed it).
    product_after = await client.get(f"/api/v1/products/{product['id']}", headers=headers)
    assert product_after.json()["stock_qty"] == 8  # 10 - 2, not -4


async def test_max_uses_exhausted_stops_applying(client):
    tokens = await signup(client, "Promo Shop K", "Owner", "promo-k@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "สินค้า", sell_price="100.00", stock_qty=10)
    await client.post(
        "/api/v1/promotions",
        json={"name": "ลดครั้งเดียว", "kind": "amount_off", "scope": "all", "amount_off": "50.00", "max_uses": 1},
        headers=headers,
    )

    first = await client.post(
        "/api/v1/sales",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"product_id": product["id"], "qty": 1}]},
        headers=headers,
    )
    assert first.status_code == 201, first.text
    assert first.json()["subtotal"] == "50.00"

    second = await client.post(
        "/api/v1/sales",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"product_id": product["id"], "qty": 1}]},
        headers=headers,
    )
    assert second.status_code == 201, second.text
    assert second.json()["subtotal"] == "100.00"  # promo exhausted, no discount


async def test_manual_discount_and_promo_discount_together_cannot_exceed_gross(client):
    tokens = await signup(client, "Promo Shop L", "Owner", "promo-l@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "สินค้า", sell_price="100.00", stock_qty=10)
    await client.post(
        "/api/v1/promotions",
        json={"name": "ลด 90%", "kind": "percent_off", "scope": "all", "percent_off": "90"},
        headers=headers,
    )

    resp = await client.post(
        "/api/v1/sales",
        json={
            "client_uuid": str(uuid.uuid4()),
            "items": [{"product_id": product["id"], "qty": 1, "discount": "20.00"}],
        },
        headers=headers,
    )
    assert resp.status_code == 400
