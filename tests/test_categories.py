from .conftest import auth_headers, signup


async def _create_category(client, headers, name="เครื่องดื่ม"):
    resp = await client.post("/api/v1/categories", json={"name": name}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_rename_category(client):
    tokens = await signup(client, "Shop A", "Owner A", "cat-a@example.com")
    headers = auth_headers(tokens)
    category = await _create_category(client, headers)

    resp = await client.patch(
        f"/api/v1/categories/{category['id']}", json={"name": "เครื่องดื่มเย็น"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "เครื่องดื่มเย็น"


async def test_delete_empty_category_succeeds(client):
    tokens = await signup(client, "Shop B", "Owner B", "cat-b@example.com")
    headers = auth_headers(tokens)
    category = await _create_category(client, headers)

    resp = await client.delete(f"/api/v1/categories/{category['id']}", headers=headers)
    assert resp.status_code == 204, resp.text

    listing = await client.get("/api/v1/categories", headers=headers)
    assert listing.json() == []


async def test_delete_category_with_products_returns_409_not_500(client):
    tokens = await signup(client, "Shop C", "Owner C", "cat-c@example.com")
    headers = auth_headers(tokens)
    category = await _create_category(client, headers)
    resp = await client.post(
        "/api/v1/products",
        json={"name": "กาแฟ", "sell_price": "45.00", "category_id": category["id"]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text

    resp = await client.delete(f"/api/v1/categories/{category['id']}", headers=headers)
    assert resp.status_code == 409
    assert "สินค้า" in resp.json()["detail"]


async def test_cross_tenant_category_patch_is_404(client):
    tokens_a = await signup(client, "Shop D", "Owner D", "cat-d@example.com")
    headers_a = auth_headers(tokens_a)
    category = await _create_category(client, headers_a)

    tokens_b = await signup(client, "Shop E", "Owner E", "cat-e@example.com")
    headers_b = auth_headers(tokens_b)

    resp = await client.patch(
        f"/api/v1/categories/{category['id']}", json={"name": "x"}, headers=headers_b
    )
    assert resp.status_code == 404
