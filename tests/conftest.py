import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import db as core_db
from app.core.db import Base, get_db
from app.core.rate_limit import reset_all as reset_rate_limits
from app.main import app

TEST_DATABASE_URL = "postgresql+asyncpg://loyverse:loyverse@localhost:5433/loyverse_test"


@pytest_asyncio.fixture
async def engine():
    # Function-scoped: asyncpg connections are bound to the event loop they were
    # created on, and pytest-asyncio gives each test its own loop, so a
    # session-scoped engine breaks on the second test ("another operation in
    # progress"). Recreating per test costs a bit of time but stays correct.
    eng = create_async_engine(TEST_DATABASE_URL)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def client(engine):
    # The auth rate limiters are module-level singletons holding failure
    # counts, so one test's bad logins would otherwise be charged to the next.
    reset_rate_limits()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    # get_session_factory() isn't reached through FastAPI's DI (background
    # jobs call it as a plain function, see app/jobs/scheduler.py), so
    # dependency_overrides can't redirect it — monkeypatch the module
    # attribute directly instead, and restore it so other tests aren't
    # left pointed at a disposed engine.
    original_get_session_factory = core_db.get_session_factory
    core_db.get_session_factory = lambda: session_factory

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
    core_db.get_session_factory = original_get_session_factory


async def signup(client, business_name, owner_name, email, password="Password123!"):
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


def auth_headers(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def create_product(client, headers, name, sell_price, cost_price="0", stock_qty=0, track_stock=True):
    resp = await client.post(
        "/api/v1/products",
        json={
            "name": name,
            "sell_price": sell_price,
            "cost_price": cost_price,
            "stock_qty": stock_qty,
            "track_stock": track_stock,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def enable_vat(client, headers, rate="7.00", price_includes_tax=True):
    resp = await client.patch(
        "/api/v1/tenant/settings",
        json={"vat_enabled": True, "vat_rate": rate, "price_includes_tax": price_includes_tax},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def enable_loyalty(client, headers, baht_per_point="25.00", point_value_baht="1.00"):
    resp = await client.patch(
        "/api/v1/tenant/settings",
        json={
            "loyalty_enabled": True,
            "baht_per_point": baht_per_point,
            "point_value_baht": point_value_baht,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def create_supplier(client, headers, name, phone=None):
    resp = await client.post("/api/v1/suppliers", json={"name": name, "phone": phone}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def create_customer(client, headers, name, phone=None):
    resp = await client.post("/api/v1/customers", json={"name": name, "phone": phone}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()
