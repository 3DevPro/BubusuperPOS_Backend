from .conftest import auth_headers, signup


async def test_owner_can_set_and_read_promptpay_id(client):
    tokens = await signup(client, "Shop A", "Owner A", "settings-a@example.com")
    headers = auth_headers(tokens)

    resp = await client.get("/api/v1/tenant/settings", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["promptpay_id"] is None

    resp = await client.patch(
        "/api/v1/tenant/settings", json={"promptpay_id": "081-234-5678"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    # non-digit characters (dashes) are stripped
    assert resp.json()["promptpay_id"] == "0812345678"

    resp = await client.get("/api/v1/tenant/settings", headers=headers)
    assert resp.json()["promptpay_id"] == "0812345678"


async def test_promptpay_id_must_be_10_or_13_digits(client):
    tokens = await signup(client, "Shop B", "Owner B", "settings-b@example.com")
    headers = auth_headers(tokens)

    resp = await client.patch("/api/v1/tenant/settings", json={"promptpay_id": "12345"}, headers=headers)
    assert resp.status_code == 422


async def test_cashier_cannot_change_settings(client):
    tokens_owner = await signup(client, "Shop C", "Owner C", "settings-c@example.com")
    headers_owner = auth_headers(tokens_owner)

    resp = await client.post(
        "/api/v1/staff",
        json={"name": "Cashier", "role": "cashier", "pin": "1234"},
        headers=headers_owner,
    )
    assert resp.status_code == 201, resp.text
    me = await client.get("/api/v1/auth/me", headers=headers_owner)
    tenant_id = me.json()["tenant_id"]

    resp = await client.post("/api/v1/auth/pin-login", json={"tenant_id": tenant_id, "pin": "1234"})
    cashier_headers = auth_headers(resp.json())

    resp = await client.patch(
        "/api/v1/tenant/settings", json={"promptpay_id": "0812345678"}, headers=cashier_headers
    )
    assert resp.status_code == 403

    # but cashiers can still read settings (view-level, not owner-only)
    resp = await client.get("/api/v1/tenant/settings", headers=cashier_headers)
    assert resp.status_code == 200


async def test_owner_can_set_vat_settings(client):
    tokens = await signup(client, "Shop D", "Owner D", "settings-d@example.com")
    headers = auth_headers(tokens)

    resp = await client.get("/api/v1/tenant/settings", headers=headers)
    assert resp.json()["vat_enabled"] is False

    resp = await client.patch(
        "/api/v1/tenant/settings",
        json={"vat_enabled": True, "vat_rate": "7.00", "price_includes_tax": False},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["vat_enabled"] is True
    assert body["vat_rate"] == "7.00"
    assert body["price_includes_tax"] is False


async def test_vat_rate_out_of_range_is_rejected(client):
    tokens = await signup(client, "Shop E", "Owner E", "settings-e@example.com")
    headers = auth_headers(tokens)

    resp = await client.patch("/api/v1/tenant/settings", json={"vat_rate": "150"}, headers=headers)
    assert resp.status_code == 422

    resp = await client.patch("/api/v1/tenant/settings", json={"vat_rate": "0"}, headers=headers)
    assert resp.status_code == 422


async def test_updating_one_field_does_not_clobber_another(client):
    # Regression guard: PATCH must only touch fields explicitly sent, not
    # overwrite every field with its unset default.
    tokens = await signup(client, "Shop F", "Owner F", "settings-f@example.com")
    headers = auth_headers(tokens)

    resp = await client.patch(
        "/api/v1/tenant/settings", json={"promptpay_id": "0812345678"}, headers=headers
    )
    assert resp.status_code == 200, resp.text

    resp = await client.patch("/api/v1/tenant/settings", json={"vat_rate": "10.00"}, headers=headers)
    assert resp.status_code == 200, resp.text

    resp = await client.get("/api/v1/tenant/settings", headers=headers)
    body = resp.json()
    assert body["promptpay_id"] == "0812345678"  # must survive the vat_rate-only PATCH
    assert body["vat_rate"] == "10.00"
