import uuid

from app.services.barcode_service import ean13_check_digit

from .conftest import auth_headers, create_product, signup


def test_ean13_check_digit_matches_known_reference():
    # 400638133393 -> 1 is a standard EAN-13 worked example.
    assert ean13_check_digit("400638133393") == "1"


def test_ean13_check_digit_rejects_wrong_length():
    try:
        ean13_check_digit("12345")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


async def test_assign_barcode_generates_in_store_ean13(client):
    tokens = await signup(client, "Shop Barcode A", "Owner", "barcode-a@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "สินค้าไม่มีบาร์โค้ด", sell_price="10.00")
    assert product["barcode"] is None

    resp = await client.post(f"/api/v1/products/{product['id']}/barcode", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["barcode"] is not None
    assert len(body["barcode"]) == 13
    assert body["barcode"].startswith("20")
    assert ean13_check_digit(body["barcode"][:12]) == body["barcode"][12]


async def test_assign_barcode_is_idempotent(client):
    tokens = await signup(client, "Shop Barcode B", "Owner", "barcode-b@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "สินค้า", sell_price="10.00")

    first = await client.post(f"/api/v1/products/{product['id']}/barcode", headers=headers)
    second = await client.post(f"/api/v1/products/{product['id']}/barcode", headers=headers)
    assert first.json()["barcode"] == second.json()["barcode"]


async def test_assign_barcode_does_not_reuse_across_products(client):
    tokens = await signup(client, "Shop Barcode C", "Owner", "barcode-c@example.com")
    headers = auth_headers(tokens)
    p1 = await create_product(client, headers, "สินค้า 1", sell_price="10.00")
    p2 = await create_product(client, headers, "สินค้า 2", sell_price="10.00")

    b1 = (await client.post(f"/api/v1/products/{p1['id']}/barcode", headers=headers)).json()["barcode"]
    b2 = (await client.post(f"/api/v1/products/{p2['id']}/barcode", headers=headers)).json()["barcode"]
    assert b1 != b2


async def test_assign_barcodes_bulk_all_missing(client):
    tokens = await signup(client, "Shop Barcode D", "Owner", "barcode-d@example.com")
    headers = auth_headers(tokens)
    p1 = await create_product(client, headers, "สินค้า 1", sell_price="10.00")
    p2 = await create_product(client, headers, "สินค้า 2", sell_price="10.00")
    resp = await client.post(
        "/api/v1/products",
        json={"name": "มีบาร์โค้ดแล้ว", "sell_price": "10.00", "barcode": "8850000000012"},
        headers=headers,
    )
    p3 = resp.json()

    resp = await client.post("/api/v1/products/assign-barcodes", json={"all_missing": True}, headers=headers)
    assert resp.status_code == 200, resp.text
    assigned_ids = {p["id"] for p in resp.json()}
    assert assigned_ids == {p1["id"], p2["id"]}

    unchanged = await client.get(f"/api/v1/products/{p3['id']}", headers=headers)
    assert unchanged.json()["barcode"] == "8850000000012"


async def test_get_product_by_barcode_exact_match(client):
    tokens = await signup(client, "Shop Barcode E", "Owner", "barcode-e@example.com")
    headers = auth_headers(tokens)
    resp = await client.post(
        "/api/v1/products",
        json={"name": "โค้ก", "sell_price": "15.00", "barcode": "8850000000012"},
        headers=headers,
    )
    product = resp.json()

    found = await client.get("/api/v1/products/by-barcode/8850000000012", headers=headers)
    assert found.status_code == 200, found.text
    assert found.json()["id"] == product["id"]

    missing = await client.get("/api/v1/products/by-barcode/0000000000000", headers=headers)
    assert missing.status_code == 404


async def test_barcode_uniqueness_scoped_per_tenant(client):
    # Two different shops may both sell "Coke" and share a manufacturer
    # barcode — the uniqueness constraint must not leak across tenants.
    tokens_a = await signup(client, "Shop Barcode F1", "Owner", "barcode-f1@example.com")
    tokens_b = await signup(client, "Shop Barcode F2", "Owner", "barcode-f2@example.com")

    resp_a = await client.post(
        "/api/v1/products",
        json={"name": "โค้ก", "sell_price": "15.00", "barcode": "8850000000099"},
        headers=auth_headers(tokens_a),
    )
    resp_b = await client.post(
        "/api/v1/products",
        json={"name": "โค้ก", "sell_price": "15.00", "barcode": "8850000000099"},
        headers=auth_headers(tokens_b),
    )
    assert resp_a.status_code == 201, resp_a.text
    assert resp_b.status_code == 201, resp_b.text


async def test_list_products_filters_by_has_barcode(client):
    tokens = await signup(client, "Shop Barcode G", "Owner", "barcode-g@example.com")
    headers = auth_headers(tokens)
    without = await create_product(client, headers, "ไม่มีบาร์โค้ด", sell_price="10.00")
    resp = await client.post(
        "/api/v1/products",
        json={"name": "มีบาร์โค้ด", "sell_price": "10.00", "barcode": "8850000000034"},
        headers=headers,
    )
    with_barcode = resp.json()

    missing = await client.get("/api/v1/products?has_barcode=false", headers=headers)
    assert [p["id"] for p in missing.json()] == [without["id"]]

    present = await client.get("/api/v1/products?has_barcode=true", headers=headers)
    assert [p["id"] for p in present.json()] == [with_barcode["id"]]


async def test_assign_barcode_missing_product_404s(client):
    tokens = await signup(client, "Shop Barcode H", "Owner", "barcode-h@example.com")
    headers = auth_headers(tokens)
    resp = await client.post(f"/api/v1/products/{uuid.uuid4()}/barcode", headers=headers)
    assert resp.status_code == 404
