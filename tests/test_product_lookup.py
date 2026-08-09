from decimal import Decimal

from app.ai.product_lookup_provider import ProductInfo
from app.core.deps import get_product_lookup_provider
from app.main import app

from .conftest import auth_headers, signup


class FakeProvider:
    """Test double for ProductLookupProvider — never touches the network.
    `calls` lets tests assert the cache actually prevents a second lookup."""

    def __init__(self, result: ProductInfo | None):
        self.result = result
        self.calls = 0

    async def lookup(self, barcode: str) -> ProductInfo | None:
        self.calls += 1
        return self.result


async def _create_cashier(client, headers, pin="1234"):
    resp = await client.post(
        "/api/v1/staff", json={"name": "Cashier", "role": "cashier", "pin": pin}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_found_barcode_returns_product_info(client):
    tokens = await signup(client, "Lookup Shop A", "Owner", "lookup-a@example.com")
    headers = auth_headers(tokens)
    fake = FakeProvider(
        ProductInfo(
            name="Coca-Cola 325ml",
            image_url="https://example.com/coke.jpg",
            price=Decimal("12.50"),
            currency="THB",
            brand="Coca-Cola",
            source="openfoodfacts",
        )
    )
    app.dependency_overrides[get_product_lookup_provider] = lambda: fake

    resp = await client.get("/api/v1/products/lookup/8850999325016", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["found"] is True
    assert body["name"] == "Coca-Cola 325ml"
    assert body["currency"] == "THB"
    assert body["cached"] is False
    assert fake.calls == 1


async def test_unknown_barcode_returns_found_false_not_error(client):
    tokens = await signup(client, "Lookup Shop B", "Owner", "lookup-b@example.com")
    headers = auth_headers(tokens)
    fake = FakeProvider(None)
    app.dependency_overrides[get_product_lookup_provider] = lambda: fake

    resp = await client.get("/api/v1/products/lookup/0000000000000", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["found"] is False
    assert body["name"] is None


async def test_repeat_lookup_hits_cache_not_provider(client):
    tokens = await signup(client, "Lookup Shop C", "Owner", "lookup-c@example.com")
    headers = auth_headers(tokens)
    fake = FakeProvider(
        ProductInfo(
            name="Ramen Snack",
            image_url=None,
            price=None,
            currency=None,
            brand=None,
            source="upcitemdb",
        )
    )
    app.dependency_overrides[get_product_lookup_provider] = lambda: fake

    first = await client.get("/api/v1/products/lookup/1234567890123", headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["cached"] is False

    second = await client.get("/api/v1/products/lookup/1234567890123", headers=headers)
    assert second.status_code == 200, second.text
    assert second.json()["cached"] is True
    assert second.json()["name"] == "Ramen Snack"

    assert fake.calls == 1


async def test_repeat_lookup_of_unknown_barcode_also_hits_cache(client):
    tokens = await signup(client, "Lookup Shop D", "Owner", "lookup-d@example.com")
    headers = auth_headers(tokens)
    fake = FakeProvider(None)
    app.dependency_overrides[get_product_lookup_provider] = lambda: fake

    await client.get("/api/v1/products/lookup/9999999999999", headers=headers)
    await client.get("/api/v1/products/lookup/9999999999999", headers=headers)

    assert fake.calls == 1


async def test_lookup_shared_across_tenants(client):
    """The cache is keyed by barcode alone (not tenant_id) — a barcode looked
    up by one shop should be served from cache for a different shop too."""
    tokens_a = await signup(client, "Lookup Shop E", "Owner", "lookup-e@example.com")
    tokens_b = await signup(client, "Lookup Shop F", "Owner", "lookup-f@example.com")
    fake = FakeProvider(
        ProductInfo(
            name="Shared Snack",
            image_url=None,
            price=None,
            currency=None,
            brand=None,
            source="openfoodfacts",
        )
    )
    app.dependency_overrides[get_product_lookup_provider] = lambda: fake

    first = await client.get("/api/v1/products/lookup/5551234567890", headers=auth_headers(tokens_a))
    assert first.json()["cached"] is False

    second = await client.get("/api/v1/products/lookup/5551234567890", headers=auth_headers(tokens_b))
    assert second.json()["cached"] is True
    assert second.json()["name"] == "Shared Snack"
    assert fake.calls == 1


async def test_cashier_without_manage_products_permission_gets_403(client):
    tokens = await signup(client, "Lookup Shop G", "Owner", "lookup-g@example.com")
    owner_headers = auth_headers(tokens)
    me = await client.get("/api/v1/auth/me", headers=owner_headers)
    tenant_id = me.json()["tenant_id"]
    await _create_cashier(client, owner_headers)

    login = await client.post("/api/v1/auth/pin-login", json={"tenant_id": tenant_id, "pin": "1234"})
    assert login.status_code == 200, login.text
    cashier_headers = auth_headers(login.json())

    app.dependency_overrides[get_product_lookup_provider] = lambda: FakeProvider(None)
    resp = await client.get("/api/v1/products/lookup/1112223334445", headers=cashier_headers)
    assert resp.status_code == 403
