import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.sale import PaymentMethod, Sale, SaleStatus
from app.models.turbo.insurance import InsurancePolicy, InsurancePolicyStatus, InsuranceProduct, InsuranceProductKind

from .conftest import auth_headers, signup

_TZ = ZoneInfo("Asia/Bangkok")


def _today():
    return datetime.now(_TZ).date()


# The real catalog is seeded by migration 1a317817145e's data insert, but
# tests build their schema from Base.metadata (see conftest.engine), which
# only creates tables — no row data — so it has to be seeded here too.
@pytest_asyncio.fixture(autouse=True)
async def _seed_insurance_products(client, engine):
    session_factory = async_sessionmaker(engine)
    async with session_factory() as session:
        session.add_all(
            [
                InsuranceProduct(
                    id=uuid.UUID("11111111-1111-4111-8111-111111111101"),
                    code="daily_income",
                    kind=InsuranceProductKind.daily_income,
                    name="ชดเชยรายได้รายวัน",
                    description="ชดเชยเมื่อร้านปิดจากเจ็บป่วยหรืออุบัติเหตุ",
                    flat_monthly_premium=Decimal("0"),
                ),
                InsuranceProduct(
                    id=uuid.UUID("11111111-1111-4111-8111-111111111102"),
                    code="accident",
                    kind=InsuranceProductKind.accident,
                    name="ไมโครประกันอุบัติเหตุ",
                    description="คุ้มครองอุบัติเหตุส่วนบุคคล",
                    flat_monthly_premium=Decimal("149"),
                ),
                InsuranceProduct(
                    id=uuid.UUID("11111111-1111-4111-8111-111111111103"),
                    code="health",
                    kind=InsuranceProductKind.health,
                    name="สุขภาพเหมาจ่ายวงเงินเล็ก",
                    description="ค่ารักษาพยาบาลเหมาจ่ายวงเงินเล็ก",
                    flat_monthly_premium=Decimal("400"),
                ),
                InsuranceProduct(
                    id=uuid.UUID("11111111-1111-4111-8111-111111111104"),
                    code="property",
                    kind=InsuranceProductKind.property,
                    name="ทรัพย์สินร้านค้า / รถเข็น",
                    description="คุ้มครองทรัพย์สินร้านค้าและรถเข็น",
                    flat_monthly_premium=Decimal("300"),
                ),
            ]
        )
        await session.commit()


async def _close(client, headers, business_date, reason="open"):
    resp = await client.post(
        "/api/v1/turbo/daily-close",
        json={"business_date": str(business_date), "closed_reason": reason},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _insert_backdated_policy(engine, tenant_id, daily_benefit, days_ago=20):
    """A claim can never predate its policy's starts_at (see
    claim_service._eligible_closes), so testing a multi-day consecutive
    claim needs a policy that's already been active for a while — which the
    purchase API can't produce (it always stamps starts_at=now()). Inserted
    directly via ORM, same technique _seed_sale uses for created_at."""
    policy_id = uuid.uuid4()
    session_factory = async_sessionmaker(engine)
    async with session_factory() as session:
        session.add(
            InsurancePolicy(
                id=policy_id,
                tenant_id=uuid.UUID(tenant_id),
                product_id=uuid.UUID("11111111-1111-4111-8111-111111111101"),
                daily_benefit=Decimal(str(daily_benefit)),
                premium_amount=Decimal("5.00"),
                premium_cycle="daily",
                status=InsurancePolicyStatus.active,
                income_profile_snapshot={},
                starts_at=datetime.now(_TZ) - timedelta(days=days_ago),
            )
        )
        await session.commit()
    return str(policy_id)


async def _seed_sale(engine, tenant_id, user_id, amount, business_date):
    local_dt = datetime.combine(business_date, datetime.min.time(), tzinfo=_TZ).replace(hour=12)
    session_factory = async_sessionmaker(engine)
    async with session_factory() as session:
        session.add(
            Sale(
                tenant_id=uuid.UUID(tenant_id),
                receipt_no=f"R{uuid.uuid4().hex[:8]}",
                user_id=uuid.UUID(user_id),
                client_uuid=uuid.uuid4(),
                subtotal=str(amount),
                discount="0",
                total=str(amount),
                payment_method=PaymentMethod.transfer_qr,
                status=SaleStatus.completed,
                created_at=local_dt,
            )
        )
        await session.commit()


async def test_list_products_returns_seeded_catalog(client):
    tokens = await signup(client, "Shop A", "Owner A", "insurance-a@example.com")
    headers = auth_headers(tokens)

    resp = await client.get("/api/v1/turbo/insurance/products", headers=headers)
    assert resp.status_code == 200, resp.text
    codes = {p["code"] for p in resp.json()}
    assert codes == {"daily_income", "accident", "health", "property"}


async def test_quote_flat_product_returns_flat_premium(client):
    tokens = await signup(client, "Shop B", "Owner B", "insurance-b@example.com")
    headers = auth_headers(tokens)

    resp = await client.get(
        "/api/v1/turbo/insurance/quote", params={"product_code": "accident"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert float(body["daily_benefit"]) == 0
    assert float(body["premium_amount"]) == 149.0
    assert body["premium_cycle"] == "monthly"


async def test_quote_daily_income_uses_income_profile(client, engine):
    tokens = await signup(client, "Shop C", "Owner C", "insurance-c@example.com")
    headers = auth_headers(tokens)
    me = await client.get("/api/v1/auth/me", headers=headers)
    tenant_id, user_id = me.json()["tenant_id"], me.json()["id"]

    await _seed_sale(engine, tenant_id, user_id, "1000.00", _today())

    resp = await client.get(
        "/api/v1/turbo/insurance/quote", params={"product_code": "daily_income"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert float(body["daily_benefit"]) == 500.0  # 50% of 1000
    assert body["premium_cycle"] == "daily"
    assert float(body["premium_amount"]) > 0


async def test_purchase_creates_active_policy(client):
    tokens = await signup(client, "Shop D", "Owner D", "insurance-d@example.com")
    headers = auth_headers(tokens)

    resp = await client.post(
        "/api/v1/turbo/insurance/policies", json={"product_code": "accident"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "active"

    listed = await client.get("/api/v1/turbo/insurance/policies", headers=headers)
    assert len(listed.json()) == 1


async def test_cannot_purchase_duplicate_active_policy(client):
    tokens = await signup(client, "Shop E", "Owner E", "insurance-e@example.com")
    headers = auth_headers(tokens)

    first = await client.post(
        "/api/v1/turbo/insurance/policies", json={"product_code": "health"}, headers=headers
    )
    assert first.status_code == 201

    second = await client.post(
        "/api/v1/turbo/insurance/policies", json={"product_code": "health"}, headers=headers
    )
    assert second.status_code == 400


async def test_detect_claimable_periods_groups_consecutive_days(client, engine):
    tokens = await signup(client, "Shop F", "Owner F", "insurance-f@example.com")
    headers = auth_headers(tokens)
    me = await client.get("/api/v1/auth/me", headers=headers)
    tenant_id = me.json()["tenant_id"]

    policy_id = await _insert_backdated_policy(engine, tenant_id, daily_benefit="500")

    # A 3-day consecutive run and one isolated day.
    await _close(client, headers, _today(), reason="sick")
    await _close(client, headers, _today() - timedelta(days=1), reason="sick")
    await _close(client, headers, _today() - timedelta(days=2), reason="accident")
    await _close(client, headers, _today() - timedelta(days=4), reason="sick")

    resp = await client.get(
        "/api/v1/turbo/insurance/claims/detected", params={"policy_id": policy_id}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    detected = resp.json()
    assert len(detected) == 2
    by_days = sorted(d["days"] for d in detected)
    assert by_days == [1, 3]
    three_day = next(d for d in detected if d["days"] == 3)
    assert float(three_day["benefit_amount"]) == 500.0 * 3


async def test_create_claim_matches_detected_and_marks_as_claimed(client, engine):
    tokens = await signup(client, "Shop G", "Owner G", "insurance-g@example.com")
    headers = auth_headers(tokens)
    me = await client.get("/api/v1/auth/me", headers=headers)
    tenant_id = me.json()["tenant_id"]

    policy_id = await _insert_backdated_policy(engine, tenant_id, daily_benefit="500")

    await _close(client, headers, _today(), reason="sick")
    await _close(client, headers, _today() - timedelta(days=1), reason="sick")

    claim_resp = await client.post(
        "/api/v1/turbo/insurance/claims",
        json={
            "policy_id": policy_id,
            "start_date": str(_today() - timedelta(days=1)),
            "end_date": str(_today()),
        },
        headers=headers,
    )
    assert claim_resp.status_code == 201, claim_resp.text
    claim = claim_resp.json()
    assert claim["days"] == 2
    assert claim["status"] == "approved"
    assert float(claim["benefit_amount"]) == 500.0 * 2

    detected_after = await client.get(
        "/api/v1/turbo/insurance/claims/detected", params={"policy_id": policy_id}, headers=headers
    )
    assert detected_after.json() == []

    listed = await client.get("/api/v1/turbo/insurance/claims", headers=headers)
    assert len(listed.json()) == 1


async def test_create_claim_rejects_dates_without_evidence(client):
    tokens = await signup(client, "Shop H", "Owner H", "insurance-h@example.com")
    headers = auth_headers(tokens)

    policy = (
        await client.post(
            "/api/v1/turbo/insurance/policies", json={"product_code": "daily_income"}, headers=headers
        )
    ).json()
    await _close(client, headers, _today())  # reason="open" — not claimable

    resp = await client.post(
        "/api/v1/turbo/insurance/claims",
        json={"policy_id": policy["id"], "start_date": str(_today()), "end_date": str(_today())},
        headers=headers,
    )
    assert resp.status_code == 400


async def test_create_claim_rejects_already_claimed_dates(client):
    tokens = await signup(client, "Shop I", "Owner I", "insurance-i@example.com")
    headers = auth_headers(tokens)

    policy = (
        await client.post(
            "/api/v1/turbo/insurance/policies", json={"product_code": "daily_income"}, headers=headers
        )
    ).json()
    await _close(client, headers, _today(), reason="sick")

    first = await client.post(
        "/api/v1/turbo/insurance/claims",
        json={"policy_id": policy["id"], "start_date": str(_today()), "end_date": str(_today())},
        headers=headers,
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        "/api/v1/turbo/insurance/claims",
        json={"policy_id": policy["id"], "start_date": str(_today()), "end_date": str(_today())},
        headers=headers,
    )
    assert second.status_code == 400


async def test_non_daily_income_policy_cannot_detect_claims(client):
    tokens = await signup(client, "Shop J", "Owner J", "insurance-j@example.com")
    headers = auth_headers(tokens)

    policy = (
        await client.post(
            "/api/v1/turbo/insurance/policies", json={"product_code": "accident"}, headers=headers
        )
    ).json()

    resp = await client.get(
        "/api/v1/turbo/insurance/claims/detected", params={"policy_id": policy["id"]}, headers=headers
    )
    assert resp.status_code == 400


async def test_cashier_cannot_access_insurance_endpoints(client):
    tokens = await signup(client, "Shop K", "Owner K", "insurance-k@example.com")
    owner_headers = auth_headers(tokens)
    me = await client.get("/api/v1/auth/me", headers=owner_headers)
    tenant_id = me.json()["tenant_id"]

    staff_resp = await client.post(
        "/api/v1/staff", json={"name": "Cashier K", "role": "cashier", "pin": "1234"}, headers=owner_headers
    )
    assert staff_resp.status_code == 201, staff_resp.text
    pin_login = await client.post("/api/v1/auth/pin-login", json={"tenant_id": tenant_id, "pin": "1234"})
    cashier_headers = auth_headers(pin_login.json())

    resp = await client.get("/api/v1/turbo/insurance/products", headers=cashier_headers)
    assert resp.status_code == 403
