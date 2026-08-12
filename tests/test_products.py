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


async def test_upload_product_image_returns_a_servable_url(client):
    tokens = await signup(client, "Shop F", "Owner F", "product-f@example.com")
    headers = auth_headers(tokens)

    resp = await client.post(
        "/api/v1/products/upload-image",
        headers=headers,
        files={"file": ("photo.jpg", b"not-a-real-jpeg-but-thats-fine-here", "image/jpeg")},
    )
    assert resp.status_code == 201, resp.text
    image_url = resp.json()["image_url"]
    assert image_url.startswith("http://test/api/v1/media/products/")
    assert image_url.endswith(".jpg")

    # Served back out through the StaticFiles mount in app/main.py.
    served = await client.get(image_url.removeprefix("http://test"))
    assert served.status_code == 200
    assert served.content == b"not-a-real-jpeg-but-thats-fine-here"


async def test_upload_product_image_rejects_non_image_content_type(client):
    tokens = await signup(client, "Shop G", "Owner G", "product-g@example.com")
    headers = auth_headers(tokens)

    resp = await client.post(
        "/api/v1/products/upload-image",
        headers=headers,
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 415


async def test_upload_product_image_rejects_oversized_file(client):
    tokens = await signup(client, "Shop H", "Owner H", "product-h@example.com")
    headers = auth_headers(tokens)

    oversized = b"0" * (5 * 1024 * 1024 + 1)
    resp = await client.post(
        "/api/v1/products/upload-image",
        headers=headers,
        files={"file": ("big.jpg", oversized, "image/jpeg")},
    )
    assert resp.status_code == 413


async def test_upload_product_image_requires_auth(client):
    resp = await client.post(
        "/api/v1/products/upload-image",
        files={"file": ("photo.jpg", b"data", "image/jpeg")},
    )
    assert resp.status_code == 401
