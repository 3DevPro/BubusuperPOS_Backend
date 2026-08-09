from .conftest import auth_headers, create_customer, signup


async def test_create_and_list_customer(client):
    tokens = await signup(client, "Shop A", "Owner A", "customers-a@example.com")
    headers = auth_headers(tokens)

    customer = await create_customer(client, headers, "คุณสมชาย", phone="0812345678")
    assert customer["points_balance"] == 0

    listed = await client.get("/api/v1/customers", headers=headers)
    assert listed.status_code == 200
    assert any(c["id"] == customer["id"] for c in listed.json())


async def test_search_customer_by_name_or_phone(client):
    tokens = await signup(client, "Shop B", "Owner B", "customers-b@example.com")
    headers = auth_headers(tokens)
    await create_customer(client, headers, "คุณสมหญิง", phone="0899999999")
    await create_customer(client, headers, "คุณวิชัย", phone="0888888888")

    by_name = await client.get("/api/v1/customers", params={"q": "สมหญิง"}, headers=headers)
    assert [c["name"] for c in by_name.json()] == ["คุณสมหญิง"]

    by_phone = await client.get("/api/v1/customers", params={"q": "8888"}, headers=headers)
    assert [c["name"] for c in by_phone.json()] == ["คุณวิชัย"]


async def test_duplicate_phone_in_same_tenant_is_rejected(client):
    tokens = await signup(client, "Shop C", "Owner C", "customers-c@example.com")
    headers = auth_headers(tokens)
    await create_customer(client, headers, "คนแรก", phone="0811111111")

    resp = await client.post(
        "/api/v1/customers", json={"name": "คนที่สอง", "phone": "0811111111"}, headers=headers
    )
    assert resp.status_code == 400


async def test_same_phone_allowed_across_different_tenants(client):
    tokens_a = await signup(client, "Shop D", "Owner D", "customers-d@example.com")
    await create_customer(client, auth_headers(tokens_a), "ลูกค้าร้านดี", phone="0822222222")

    tokens_b = await signup(client, "Shop E", "Owner E", "customers-e@example.com")
    resp = await client.post(
        "/api/v1/customers", json={"name": "ลูกค้าร้านอี", "phone": "0822222222"}, headers=auth_headers(tokens_b)
    )
    assert resp.status_code == 201, resp.text


async def test_customer_does_not_leak_across_tenants(client):
    tokens_a = await signup(client, "Shop F", "Owner F", "customers-f@example.com")
    await create_customer(client, auth_headers(tokens_a), "ลูกค้าลับ", phone="0833333333")

    tokens_b = await signup(client, "Shop G", "Owner G", "customers-g@example.com")
    listed = await client.get("/api/v1/customers", headers=auth_headers(tokens_b))
    assert listed.json() == []


async def test_update_customer(client):
    tokens = await signup(client, "Shop H", "Owner H", "customers-h@example.com")
    headers = auth_headers(tokens)
    customer = await create_customer(client, headers, "ชื่อเดิม", phone="0844444444")

    resp = await client.patch(
        f"/api/v1/customers/{customer['id']}", json={"name": "ชื่อใหม่"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "ชื่อใหม่"
    assert resp.json()["phone"] == "0844444444"


async def test_cashier_can_create_and_list_customers(client):
    """Unlike products, customer management is granted to all three roles —
    it's a normal part of the checkout flow a cashier performs."""
    tokens = await signup(client, "Shop I", "Owner I", "customers-i@example.com")
    headers = auth_headers(tokens)
    cashier_resp = await client.post(
        "/api/v1/staff", json={"name": "Cashier", "role": "cashier", "pin": "1234"}, headers=headers
    )
    assert cashier_resp.status_code == 201, cashier_resp.text
    me = await client.get("/api/v1/auth/me", headers=headers)
    tenant_id = me.json()["tenant_id"]
    login = await client.post("/api/v1/auth/pin-login", json={"tenant_id": tenant_id, "pin": "1234"})
    cashier_headers = auth_headers(login.json())

    resp = await client.post(
        "/api/v1/customers", json={"name": "ลูกค้าจากแคชเชียร์"}, headers=cashier_headers
    )
    assert resp.status_code == 201, resp.text

    listed = await client.get("/api/v1/customers", headers=cashier_headers)
    assert listed.status_code == 200
