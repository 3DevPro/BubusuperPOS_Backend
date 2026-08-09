from .conftest import auth_headers, signup


async def _create_cashier(client, headers, name="Cashier 1", pin="1234"):
    resp = await client.post(
        "/api/v1/staff", json={"name": name, "role": "cashier", "pin": pin}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_owner_can_change_staff_role(client):
    tokens = await signup(client, "Shop A", "Owner A", "staff-a@example.com")
    headers = auth_headers(tokens)
    cashier = await _create_cashier(client, headers)

    resp = await client.patch(f"/api/v1/staff/{cashier['id']}", json={"role": "manager"}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "manager"


async def test_owner_can_reset_staff_pin(client):
    tokens = await signup(client, "Shop B", "Owner B", "staff-b@example.com")
    headers = auth_headers(tokens)
    cashier = await _create_cashier(client, headers, pin="1234")
    me = await client.get("/api/v1/auth/me", headers=headers)
    tenant_id = me.json()["tenant_id"]

    resp = await client.patch(f"/api/v1/staff/{cashier['id']}", json={"pin": "5678"}, headers=headers)
    assert resp.status_code == 200, resp.text

    old_pin = await client.post("/api/v1/auth/pin-login", json={"tenant_id": tenant_id, "pin": "1234"})
    assert old_pin.status_code == 401

    new_pin = await client.post("/api/v1/auth/pin-login", json={"tenant_id": tenant_id, "pin": "5678"})
    assert new_pin.status_code == 200, new_pin.text


async def test_deactivated_staff_cannot_pin_login(client):
    tokens = await signup(client, "Shop C", "Owner C", "staff-c@example.com")
    headers = auth_headers(tokens)
    cashier = await _create_cashier(client, headers, pin="1234")
    me = await client.get("/api/v1/auth/me", headers=headers)
    tenant_id = me.json()["tenant_id"]

    resp = await client.patch(f"/api/v1/staff/{cashier['id']}", json={"is_active": False}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is False

    login = await client.post("/api/v1/auth/pin-login", json={"tenant_id": tenant_id, "pin": "1234"})
    assert login.status_code == 401


async def test_owner_cannot_deactivate_self(client):
    tokens = await signup(client, "Shop D", "Owner D", "staff-d@example.com")
    headers = auth_headers(tokens)
    me = await client.get("/api/v1/auth/me", headers=headers)
    owner_id = me.json()["id"]

    resp = await client.patch(f"/api/v1/staff/{owner_id}", json={"is_active": False}, headers=headers)
    assert resp.status_code == 400


async def test_cannot_demote_last_active_owner(client):
    tokens = await signup(client, "Shop E", "Owner E", "staff-e@example.com")
    headers = auth_headers(tokens)
    me = await client.get("/api/v1/auth/me", headers=headers)
    owner_id = me.json()["id"]

    resp = await client.patch(f"/api/v1/staff/{owner_id}", json={"role": "manager"}, headers=headers)
    assert resp.status_code == 400


async def test_second_owner_deactivation_allowed_but_last_one_blocked(client):
    tokens = await signup(client, "Shop F", "Owner F", "staff-f@example.com")
    headers = auth_headers(tokens)
    resp = await client.post(
        "/api/v1/staff", json={"name": "Owner G", "role": "owner", "pin": "1111"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    second_owner_id = resp.json()["id"]
    me = await client.get("/api/v1/auth/me", headers=headers)
    first_owner_id = me.json()["id"]

    # Deactivating the second owner is fine — one active owner remains.
    resp = await client.patch(
        f"/api/v1/staff/{second_owner_id}", json={"is_active": False}, headers=headers
    )
    assert resp.status_code == 200, resp.text

    # Now the first owner is the last one left — must be blocked.
    resp = await client.patch(f"/api/v1/staff/{first_owner_id}", json={"role": "manager"}, headers=headers)
    assert resp.status_code == 400


async def test_cashier_cannot_patch_staff(client):
    tokens = await signup(client, "Shop H", "Owner H", "staff-h@example.com")
    headers = auth_headers(tokens)
    cashier = await _create_cashier(client, headers)
    me = await client.get("/api/v1/auth/me", headers=headers)
    tenant_id = me.json()["tenant_id"]

    login = await client.post("/api/v1/auth/pin-login", json={"tenant_id": tenant_id, "pin": "1234"})
    cashier_headers = auth_headers(login.json())

    resp = await client.patch(
        f"/api/v1/staff/{cashier['id']}", json={"role": "manager"}, headers=cashier_headers
    )
    assert resp.status_code == 403


async def test_cashier_cannot_list_staff(client):
    tokens = await signup(client, "Shop K", "Owner K", "staff-k@example.com")
    headers = auth_headers(tokens)
    await _create_cashier(client, headers)
    me = await client.get("/api/v1/auth/me", headers=headers)
    tenant_id = me.json()["tenant_id"]

    login = await client.post("/api/v1/auth/pin-login", json={"tenant_id": tenant_id, "pin": "1234"})
    cashier_headers = auth_headers(login.json())

    resp = await client.get("/api/v1/staff", headers=cashier_headers)
    assert resp.status_code == 403


async def test_cross_tenant_staff_patch_is_404(client):
    tokens_a = await signup(client, "Shop I", "Owner I", "staff-i@example.com")
    headers_a = auth_headers(tokens_a)
    cashier = await _create_cashier(client, headers_a)

    tokens_b = await signup(client, "Shop J", "Owner J", "staff-j@example.com")
    headers_b = auth_headers(tokens_b)

    resp = await client.patch(
        f"/api/v1/staff/{cashier['id']}", json={"role": "manager"}, headers=headers_b
    )
    assert resp.status_code == 404


async def test_duplicate_pin_within_shop_is_rejected(client):
    """Two staff with the same PIN would make pin_login ambiguous — whoever
    the scan reaches first wins, so a cashier could land in the owner's
    session."""
    tokens = await signup(client, "Shop PIN", "Owner PIN", "staff-pin@example.com")
    headers = auth_headers(tokens)

    first = await client.post(
        "/api/v1/staff", json={"name": "แคชเชียร์เอ", "role": "cashier", "pin": "4321"}, headers=headers
    )
    assert first.status_code == 201, first.text

    clash = await client.post(
        "/api/v1/staff", json={"name": "แคชเชียร์บี", "role": "cashier", "pin": "4321"}, headers=headers
    )
    assert clash.status_code == 400
    assert "แคชเชียร์เอ" in clash.json()["detail"]

    ok = await client.post(
        "/api/v1/staff", json={"name": "แคชเชียร์บี", "role": "cashier", "pin": "5678"}, headers=headers
    )
    assert ok.status_code == 201, ok.text

    # Resetting someone's PIN to one already in use is blocked the same way,
    # but re-setting a user's own PIN to what it already is must not trip it.
    clash_update = await client.patch(
        f"/api/v1/staff/{ok.json()['id']}", json={"pin": "4321"}, headers=headers
    )
    assert clash_update.status_code == 400

    same_pin_again = await client.patch(
        f"/api/v1/staff/{ok.json()['id']}", json={"pin": "5678"}, headers=headers
    )
    assert same_pin_again.status_code == 200, same_pin_again.text


async def test_duplicate_pin_is_scoped_to_the_shop(client):
    """Another shop using the same PIN is not a clash — pin_login only ever
    scans one tenant."""
    a = auth_headers(await signup(client, "Shop PIN A", "Owner A", "staff-pin-a@example.com"))
    b = auth_headers(await signup(client, "Shop PIN B", "Owner B", "staff-pin-b@example.com"))

    first = await client.post("/api/v1/staff", json={"name": "พนักงาน", "pin": "1111"}, headers=a)
    assert first.status_code == 201, first.text

    other_shop = await client.post("/api/v1/staff", json={"name": "พนักงาน", "pin": "1111"}, headers=b)
    assert other_shop.status_code == 201, other_shop.text
