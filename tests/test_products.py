from .conftest import auth_headers, create_product, signup


async def test_get_product_by_id(client):
    tokens = await signup(client, "Shop A", "Owner A", "product-a@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "กาแฟ", sell_price="45.00", stock_qty=10)

    resp = await client.get(f"/api/v1/products/{product['id']}", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "กาแฟ"


async def test_get_product_by_id_404_when_missing(client):
    tokens = await signup(client, "Shop B", "Owner B", "product-b@example.com")
    headers = auth_headers(tokens)

    resp = await client.get("/api/v1/products/00000000-0000-0000-0000-000000000000", headers=headers)
    assert resp.status_code == 404


async def test_get_product_by_id_404_for_inactive(client):
    tokens = await signup(client, "Shop C", "Owner C", "product-c@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "ชา", sell_price="40.00", stock_qty=10)

    resp = await client.delete(f"/api/v1/products/{product['id']}", headers=headers)
    assert resp.status_code == 204, resp.text

    resp = await client.get(f"/api/v1/products/{product['id']}", headers=headers)
    assert resp.status_code == 404


async def test_list_products_respects_limit(client):
    tokens = await signup(client, "Shop D", "Owner D", "product-d@example.com")
    headers = auth_headers(tokens)
    for name in ["กาแฟ", "ชา", "นม"]:
        await create_product(client, headers, name, sell_price="20.00", stock_qty=5)

    resp = await client.get("/api/v1/products?limit=2", headers=headers)
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 2


async def test_list_products_without_limit_returns_all(client):
    tokens = await signup(client, "Shop E", "Owner E", "product-e@example.com")
    headers = auth_headers(tokens)
    for name in ["กาแฟ", "ชา", "นม"]:
        await create_product(client, headers, name, sell_price="20.00", stock_qty=5)

    resp = await client.get("/api/v1/products", headers=headers)
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 3
