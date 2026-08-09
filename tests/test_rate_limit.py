import uuid

from app.core.rate_limit import FailureLimiter, pin_login_limiter

from .conftest import auth_headers, signup


async def _pin_login(client, tenant_id, pin):
    return await client.post("/api/v1/auth/pin-login", json={"tenant_id": tenant_id, "pin": pin})


async def test_pin_login_is_capped_after_repeated_wrong_pins(client):
    tokens = await signup(client, "Shop RL", "Owner RL", "ratelimit@example.com")
    headers = auth_headers(tokens)
    tenant_id = (await client.get("/api/v1/auth/me", headers=headers)).json()["tenant_id"]

    created = await client.post(
        "/api/v1/staff", json={"name": "แคชเชียร์", "role": "cashier", "pin": "1234"}, headers=headers
    )
    assert created.status_code == 201, created.text

    for _ in range(pin_login_limiter.max_failures):
        assert (await _pin_login(client, tenant_id, "9999")).status_code == 401

    blocked = await _pin_login(client, tenant_id, "9999")
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0

    # The cap has to hold even against the correct PIN, or an attacker gets
    # unlimited tries as long as the last one happens to be right.
    assert (await _pin_login(client, tenant_id, "1234")).status_code == 429


async def test_pin_login_cap_is_per_shop(client):
    """One shop being brute-forced must not lock every other shop out."""
    a = await signup(client, "Shop RL A", "Owner A", "ratelimit-a@example.com")
    b = await signup(client, "Shop RL B", "Owner B", "ratelimit-b@example.com")
    a_tenant = (await client.get("/api/v1/auth/me", headers=auth_headers(a))).json()["tenant_id"]
    b_tenant = (await client.get("/api/v1/auth/me", headers=auth_headers(b))).json()["tenant_id"]

    await client.post(
        "/api/v1/staff", json={"name": "พนักงานบี", "pin": "4321"}, headers=auth_headers(b)
    )

    for _ in range(pin_login_limiter.max_failures + 1):
        await _pin_login(client, a_tenant, "9999")
    assert (await _pin_login(client, a_tenant, "9999")).status_code == 429

    assert (await _pin_login(client, b_tenant, "4321")).status_code == 200


async def test_login_cap_lifts_after_a_success(client):
    """A cashier who mistypes a few times and then gets it right starts clean
    — only unbroken runs of failures count."""
    await signup(client, "Shop RL C", "Owner C", "ratelimit-c@example.com", password="Password123!")

    for _ in range(pin_login_limiter.max_failures - 1):
        resp = await client.post(
            "/api/v1/auth/login", json={"email": "ratelimit-c@example.com", "password": "wrong"}
        )
        assert resp.status_code == 401

    good = await client.post(
        "/api/v1/auth/login", json={"email": "ratelimit-c@example.com", "password": "Password123!"}
    )
    assert good.status_code == 200

    for _ in range(pin_login_limiter.max_failures - 1):
        resp = await client.post(
            "/api/v1/auth/login", json={"email": "ratelimit-c@example.com", "password": "wrong"}
        )
        assert resp.status_code == 401


async def test_unknown_email_is_capped_too(client):
    """Guessing which emails exist is itself worth slowing down, so a miss
    counts the same as a wrong password."""
    for _ in range(pin_login_limiter.max_failures):
        resp = await client.post(
            "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong"}
        )
        assert resp.status_code == 401

    blocked = await client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong"}
    )
    assert blocked.status_code == 429


def test_failures_age_out_of_the_window():
    limiter = FailureLimiter(max_failures=3, window_seconds=0)
    key = str(uuid.uuid4())
    for _ in range(5):
        limiter.record_failure(key)
    # Every recorded failure is already older than a zero-length window.
    assert limiter.retry_after(key) is None


def test_retry_after_counts_down_to_the_oldest_failure():
    limiter = FailureLimiter(max_failures=2, window_seconds=600)
    key = str(uuid.uuid4())
    limiter.record_failure(key)
    limiter.record_failure(key)

    retry_after = limiter.retry_after(key)
    assert retry_after is not None
    assert 0 < retry_after <= 601

    limiter.clear(key)
    assert limiter.retry_after(key) is None
