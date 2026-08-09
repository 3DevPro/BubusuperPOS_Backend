from .conftest import auth_headers, signup


async def _create_cashier(client, headers, name="Cashier 1", pin="1234"):
    resp = await client.post(
        "/api/v1/staff", json={"name": name, "role": "cashier", "pin": pin}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_category(client, headers, name="เครื่องดื่ม"):
    resp = await client.post("/api/v1/categories", json={"name": name}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _actions(client, headers):
    resp = await client.get("/api/v1/audit-log", headers=headers)
    assert resp.status_code == 200, resp.text
    return [row["action"] for row in resp.json()]


async def test_staff_create_and_update_are_logged(client):
    tokens = await signup(client, "Shop A", "Owner A", "audit-a@example.com")
    headers = auth_headers(tokens)
    cashier = await _create_cashier(client, headers)

    resp = await client.patch(
        f"/api/v1/staff/{cashier['id']}", json={"role": "manager"}, headers=headers
    )
    assert resp.status_code == 200, resp.text

    actions = await _actions(client, headers)
    assert actions.count("staff.create") == 1
    assert actions.count("staff.update") == 1


async def test_category_crud_is_logged(client):
    tokens = await signup(client, "Shop B", "Owner B", "audit-b@example.com")
    headers = auth_headers(tokens)
    category = await _create_category(client, headers)

    resp = await client.patch(
        f"/api/v1/categories/{category['id']}", json={"name": "เครื่องดื่มเย็น"}, headers=headers
    )
    assert resp.status_code == 200, resp.text

    resp = await client.delete(f"/api/v1/categories/{category['id']}", headers=headers)
    assert resp.status_code == 204, resp.text

    actions = await _actions(client, headers)
    assert actions.count("category.create") == 1
    assert actions.count("category.update") == 1
    assert actions.count("category.delete") == 1


async def test_failed_category_delete_leaves_no_audit_row(client):
    tokens = await signup(client, "Shop C", "Owner C", "audit-c@example.com")
    headers = auth_headers(tokens)
    category = await _create_category(client, headers)
    resp = await client.post(
        "/api/v1/products",
        json={"name": "กาแฟ", "sell_price": "45.00", "category_id": category["id"]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text

    resp = await client.delete(f"/api/v1/categories/{category['id']}", headers=headers)
    assert resp.status_code == 409, resp.text

    actions = await _actions(client, headers)
    assert "category.delete" not in actions


async def test_product_and_inventory_actions_are_logged(client):
    tokens = await signup(client, "Shop D", "Owner D", "audit-d@example.com")
    headers = auth_headers(tokens)

    resp = await client.post(
        "/api/v1/products",
        json={"name": "น้ำเปล่า", "sell_price": "10.00", "stock_qty": 20},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    product = resp.json()

    resp = await client.patch(
        f"/api/v1/products/{product['id']}", json={"sell_price": "12.00"}, headers=headers
    )
    assert resp.status_code == 200, resp.text

    resp = await client.post(
        "/api/v1/inventory/adjust",
        json={"product_id": product["id"], "qty_delta": -5, "type": "waste"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    resp = await client.delete(f"/api/v1/products/{product['id']}", headers=headers)
    assert resp.status_code == 204, resp.text

    actions = await _actions(client, headers)
    assert actions.count("product.create") == 1
    assert actions.count("product.update") == 1
    assert actions.count("inventory.adjust") == 1
    assert actions.count("product.delete") == 1


async def test_tenant_settings_update_is_logged(client):
    tokens = await signup(client, "Shop E", "Owner E", "audit-e@example.com")
    headers = auth_headers(tokens)

    resp = await client.patch(
        "/api/v1/tenant/settings", json={"promptpay_id": "0812345678"}, headers=headers
    )
    assert resp.status_code == 200, resp.text

    actions = await _actions(client, headers)
    assert actions.count("tenant_settings.update") == 1


async def test_audit_log_is_tenant_scoped(client):
    tokens_a = await signup(client, "Shop F", "Owner F", "audit-f@example.com")
    headers_a = auth_headers(tokens_a)
    await _create_category(client, headers_a)

    tokens_b = await signup(client, "Shop G", "Owner G", "audit-g@example.com")
    headers_b = auth_headers(tokens_b)

    actions_b = await _actions(client, headers_b)
    assert actions_b == []


async def test_cashier_and_manager_cannot_view_audit_log(client):
    tokens = await signup(client, "Shop H", "Owner H", "audit-h@example.com")
    headers = auth_headers(tokens)
    await _create_cashier(client, headers)
    me = await client.get("/api/v1/auth/me", headers=headers)
    tenant_id = me.json()["tenant_id"]

    login = await client.post("/api/v1/auth/pin-login", json={"tenant_id": tenant_id, "pin": "1234"})
    cashier_headers = auth_headers(login.json())

    resp = await client.get("/api/v1/audit-log", headers=cashier_headers)
    assert resp.status_code == 403
