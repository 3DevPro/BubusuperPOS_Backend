async def _signup(client, business_name, owner_name, email, password="Password123!"):
    resp = await client.post(
        "/api/v1/auth/signup",
        json={
            "business_name": business_name,
            "owner_name": owner_name,
            "email": email,
            "password": password,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_signup_and_me(client):
    tokens = await _signup(client, "Shop A", "Owner A", "ownera@example.com")

    resp = await client.get("/api/v1/auth/me", headers=_auth(tokens))
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "owner"
    assert body["name"] == "Owner A"


async def test_login_with_wrong_password_rejected(client):
    await _signup(client, "Shop A", "Owner A", "wrongpw@example.com")

    resp = await client.post(
        "/api/v1/auth/login", json={"email": "wrongpw@example.com", "password": "nope"}
    )
    assert resp.status_code == 401


async def test_tenant_isolation_on_products(client):
    tokens_a = await _signup(client, "Shop A", "Owner A", "isoa@example.com")
    tokens_b = await _signup(client, "Shop B", "Owner B", "isob@example.com")

    resp = await client.post(
        "/api/v1/products",
        json={"name": "กาแฟ", "sell_price": "45.00"},
        headers=_auth(tokens_a),
    )
    assert resp.status_code == 201, resp.text

    resp_a = await client.get("/api/v1/products", headers=_auth(tokens_a))
    assert len(resp_a.json()) == 1
    assert resp_a.json()[0]["name"] == "กาแฟ"

    # Tenant B must NOT see tenant A's product — this is the core multi-tenant
    # isolation guarantee the whole `tenant_id`-from-JWT design exists for.
    resp_b = await client.get("/api/v1/products", headers=_auth(tokens_b))
    assert resp_b.json() == []


async def test_cashier_pin_login_and_permission_scope(client):
    tokens_owner = await _signup(client, "Shop C", "Owner C", "ownerc@example.com")

    resp = await client.post(
        "/api/v1/staff",
        json={"name": "Cashier 1", "role": "cashier", "pin": "1234"},
        headers=_auth(tokens_owner),
    )
    assert resp.status_code == 201, resp.text

    me = await client.get("/api/v1/auth/me", headers=_auth(tokens_owner))
    tenant_id = me.json()["tenant_id"]

    resp = await client.post(
        "/api/v1/auth/pin-login", json={"tenant_id": tenant_id, "pin": "1234"}
    )
    assert resp.status_code == 200, resp.text
    cashier_headers = _auth(resp.json())

    # cashiers can read the catalog...
    resp = await client.get("/api/v1/products", headers=cashier_headers)
    assert resp.status_code == 200

    # ...but not create/edit products (manage_products is owner/manager only)
    resp = await client.post(
        "/api/v1/products",
        json={"name": "น้ำแข็ง", "sell_price": "10"},
        headers=cashier_headers,
    )
    assert resp.status_code == 403


async def test_pin_login_wrong_pin_rejected(client):
    tokens_owner = await _signup(client, "Shop D", "Owner D", "ownerd@example.com")
    await client.post(
        "/api/v1/staff",
        json={"name": "Cashier D", "role": "cashier", "pin": "1234"},
        headers=_auth(tokens_owner),
    )
    me = await client.get("/api/v1/auth/me", headers=_auth(tokens_owner))
    tenant_id = me.json()["tenant_id"]

    resp = await client.post(
        "/api/v1/auth/pin-login", json={"tenant_id": tenant_id, "pin": "9999"}
    )
    assert resp.status_code == 401


async def test_duplicate_signup_email_is_a_conflict(client):
    await _signup(client, "Shop Dup", "Owner Dup", "duplicate@example.com")

    resp = await client.post(
        "/api/v1/auth/signup",
        json={
            "business_name": "Another Shop",
            "owner_name": "Someone Else",
            "email": "duplicate@example.com",
            "password": "Password123!",
        },
    )
    assert resp.status_code == 409, resp.text
