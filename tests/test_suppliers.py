from .conftest import auth_headers, create_supplier, signup


async def test_create_and_list_supplier(client):
    tokens = await signup(client, "Shop A", "Owner A", "suppliers-a@example.com")
    headers = auth_headers(tokens)

    supplier = await create_supplier(client, headers, "ร้านขายส่งเอบีซี", phone="021234567")
    listed = await client.get("/api/v1/suppliers", headers=headers)
    assert listed.status_code == 200
    assert any(s["id"] == supplier["id"] for s in listed.json())


async def test_search_supplier_by_name_or_phone(client):
    tokens = await signup(client, "Shop B", "Owner B", "suppliers-b@example.com")
    headers = auth_headers(tokens)
    await create_supplier(client, headers, "ฟาร์มผักสด", phone="0899999999")
    await create_supplier(client, headers, "โรงงานขนม", phone="0888888888")

    by_name = await client.get("/api/v1/suppliers", params={"q": "ผักสด"}, headers=headers)
    assert [s["name"] for s in by_name.json()] == ["ฟาร์มผักสด"]

    by_phone = await client.get("/api/v1/suppliers", params={"q": "8888"}, headers=headers)
    assert [s["name"] for s in by_phone.json()] == ["โรงงานขนม"]


async def test_supplier_does_not_leak_across_tenants(client):
    tokens_a = await signup(client, "Shop C", "Owner C", "suppliers-c@example.com")
    await create_supplier(client, auth_headers(tokens_a), "ซัพพลายเออร์ลับ")

    tokens_b = await signup(client, "Shop D", "Owner D", "suppliers-d@example.com")
    listed = await client.get("/api/v1/suppliers", headers=auth_headers(tokens_b))
    assert listed.json() == []


async def test_update_supplier(client):
    tokens = await signup(client, "Shop E", "Owner E", "suppliers-e@example.com")
    headers = auth_headers(tokens)
    supplier = await create_supplier(client, headers, "ชื่อเดิม")

    resp = await client.patch(
        f"/api/v1/suppliers/{supplier['id']}", json={"name": "ชื่อใหม่", "email": "new@supplier.com"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "ชื่อใหม่"
    assert resp.json()["email"] == "new@supplier.com"


async def test_cashier_denied_supplier_access(client):
    tokens = await signup(client, "Shop F", "Owner F", "suppliers-f@example.com")
    headers = auth_headers(tokens)
    cashier_resp = await client.post(
        "/api/v1/staff", json={"name": "Cashier", "role": "cashier", "pin": "1234"}, headers=headers
    )
    assert cashier_resp.status_code == 201, cashier_resp.text
    me = await client.get("/api/v1/auth/me", headers=headers)
    tenant_id = me.json()["tenant_id"]
    login = await client.post("/api/v1/auth/pin-login", json={"tenant_id": tenant_id, "pin": "1234"})
    cashier_headers = auth_headers(login.json())

    resp = await client.post("/api/v1/suppliers", json={"name": "ห้ามเพิ่ม"}, headers=cashier_headers)
    assert resp.status_code == 403

    listed = await client.get("/api/v1/suppliers", headers=cashier_headers)
    assert listed.status_code == 403
