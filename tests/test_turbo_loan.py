import asyncio
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.turbo.branch import Branch
from app.models.turbo.loan import LoanAccount, LoanCollateralKind, LoanProduct
from app.services.turbo.loan_service import amortized_installment, build_schedule

from .conftest import auth_headers, signup

_TZ = ZoneInfo("Asia/Bangkok")

_MOTORCYCLE_ID = uuid.UUID("22222222-2222-4222-8222-222222222201")
_CAR_ID = uuid.UUID("22222222-2222-4222-8222-222222222202")
_TINY_CEILING_ID = uuid.UUID("22222222-2222-4222-8222-222222222299")


def _today():
    return datetime.now(_TZ).date()


# The real catalog is seeded by a migration, but tests build their schema
# from Base.metadata (see conftest.engine), which only creates tables — no
# row data — so it has to be seeded here too, same technique as
# test_turbo_insurance.py's _seed_insurance_products.
@pytest_asyncio.fixture(autouse=True)
async def _seed_loan_products(client, engine):
    session_factory = async_sessionmaker(engine)
    async with session_factory() as session:
        session.add_all(
            [
                # apply() always routes the resulting Lead to a branch (see
                # loan_service.apply -> pick_branch_for_province), so every
                # test that applies for a loan needs at least one to exist.
                Branch(code="LOAN-TEST-01", name="สาขาทดสอบ", province="กรุงเทพ"),
                LoanProduct(
                    id=_MOTORCYCLE_ID,
                    code="motorcycle",
                    collateral_kind=LoanCollateralKind.motorcycle,
                    name="สินเชื่อรถมอเตอร์ไซค์",
                    description="ใช้มอเตอร์ไซค์เป็นหลักประกัน",
                    max_principal=Decimal("100000"),
                    monthly_interest_rate=Decimal("0.0200"),
                    min_term_months=6,
                    max_term_months=36,
                ),
                LoanProduct(
                    id=_CAR_ID,
                    code="car",
                    collateral_kind=LoanCollateralKind.car,
                    name="สินเชื่อรถยนต์",
                    description="ใช้รถยนต์เป็นหลักประกัน",
                    max_principal=Decimal("1000000"),
                    monthly_interest_rate=Decimal("0.0125"),
                    min_term_months=6,
                    max_term_months=60,
                ),
                # A distinct low-ceiling product to exercise the
                # "product's own max_principal" cap specifically — the real
                # catalog's ceilings are all far above TIER_1_CREDIT_LIMIT,
                # so that cap alone can never bind against a tier_1 tenant.
                LoanProduct(
                    id=_TINY_CEILING_ID,
                    code="tiny_ceiling",
                    collateral_kind=LoanCollateralKind.motorcycle,
                    name="ทดสอบเพดานต่ำ",
                    description="สำหรับทดสอบ product max_principal cap",
                    max_principal=Decimal("3000"),
                    monthly_interest_rate=Decimal("0.0200"),
                    min_term_months=6,
                    max_term_months=36,
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


async def _make_tier_1_tenant(client, business_name, owner_name, email):
    """A tenant with a full 30-day streak — tier_1, credit_limit ฿10,000 —
    the same setup test_turbo_income.py's streak test uses."""
    tokens = await signup(client, business_name, owner_name, email)
    headers = auth_headers(tokens)
    for offset in range(30):
        await _close(client, headers, _today() - timedelta(days=offset))
    return headers


def test_amortized_installment_matches_hand_calculation():
    # ฿10,000 at 2%/month over 12 months — same numbers the seeded demo
    # loan account uses, computed by hand from the standard reducing-balance
    # formula P*i/(1-(1+i)^-n).
    installment = amortized_installment(Decimal("10000"), Decimal("0.02"), 12)
    assert installment == Decimal("945.60")


def test_amortized_installment_zero_rate_splits_evenly():
    installment = amortized_installment(Decimal("12000"), Decimal("0"), 12)
    assert installment == Decimal("1000.00")


def test_build_schedule_principal_sums_exactly_with_no_drift():
    principal = Decimal("10000")
    schedule = build_schedule(principal, Decimal("0.02"), 12, date(2026, 1, 15))

    total_principal = sum((row[2] for row in schedule), Decimal("0"))
    assert total_principal == principal

    # Due dates land on the same day-of-month, one month apart.
    due_dates = [row[1] for row in schedule]
    assert due_dates[0] == date(2026, 1, 15)
    assert due_dates[1] == date(2026, 2, 15)
    assert due_dates[-1] == date(2026, 12, 15)

    # Interest declines and principal component grows every period on a
    # reducing balance — a basic sanity check that the schedule isn't just
    # flat installments mislabeled as amortized.
    assert schedule[0][3] > schedule[-1][3]  # interest_component
    assert schedule[0][2] < schedule[-1][2]  # principal_component


async def test_list_products_returns_seeded_catalog(client):
    tokens = await signup(client, "Shop A", "Owner A", "loan-a@example.com")
    headers = auth_headers(tokens)

    resp = await client.get("/api/v1/turbo/loans/products", headers=headers)
    assert resp.status_code == 200, resp.text
    codes = {p["code"] for p in resp.json()}
    assert {"motorcycle", "car", "tiny_ceiling"} <= codes


async def test_quote_rejected_when_credit_not_unlocked(client):
    tokens = await signup(client, "Shop B", "Owner B", "loan-b@example.com")
    headers = auth_headers(tokens)

    resp = await client.post(
        "/api/v1/turbo/loans/quote",
        json={"product_code": "motorcycle", "requested_amount": "5000", "collateral_value": "20000", "term_months": 12},
        headers=headers,
    )
    assert resp.status_code == 400


async def test_quote_capped_by_credit_tier(client):
    headers = await _make_tier_1_tenant(client, "Shop C", "Owner C", "loan-c@example.com")

    resp = await client.post(
        "/api/v1/turbo/loans/quote",
        json={
            "product_code": "motorcycle",
            "requested_amount": "50000",
            "collateral_value": "200000",  # LTV cap (70%) = 140,000 — not binding
            "term_months": 12,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert float(body["approved_amount"]) == 10000.0
    assert len(body["cap_reasons"]) == 1
    assert "10,000" in body["cap_reasons"][0]
    assert float(body["monthly_installment"]) > 0
    assert float(body["total_repayment"]) > float(body["approved_amount"])


async def test_quote_capped_by_collateral_ltv(client):
    headers = await _make_tier_1_tenant(client, "Shop D", "Owner D", "loan-d@example.com")

    resp = await client.post(
        "/api/v1/turbo/loans/quote",
        json={
            "product_code": "motorcycle",
            "requested_amount": "10000",  # within credit_limit
            "collateral_value": "5000",  # LTV cap (70%) = 3,500 — the binding cap
            "term_months": 12,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert float(body["approved_amount"]) == 3500.0
    assert any("หลักประกัน" in reason for reason in body["cap_reasons"])


async def test_quote_capped_by_product_ceiling(client):
    headers = await _make_tier_1_tenant(client, "Shop E", "Owner E", "loan-e@example.com")

    resp = await client.post(
        "/api/v1/turbo/loans/quote",
        json={
            "product_code": "tiny_ceiling",
            "requested_amount": "10000",  # within credit_limit
            "collateral_value": "1000000",  # LTV cap far above — not binding
            "term_months": 12,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert float(body["approved_amount"]) == 3000.0
    assert any("เพดานสินเชื่อ" in reason for reason in body["cap_reasons"])


async def test_quote_not_capped_when_within_every_limit(client):
    headers = await _make_tier_1_tenant(client, "Shop F", "Owner F", "loan-f@example.com")

    resp = await client.post(
        "/api/v1/turbo/loans/quote",
        json={
            "product_code": "motorcycle",
            "requested_amount": "3000",
            "collateral_value": "100000",
            "term_months": 12,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert float(body["approved_amount"]) == 3000.0
    assert body["cap_reasons"] == []


async def test_term_months_outside_product_range_rejected(client):
    headers = await _make_tier_1_tenant(client, "Shop G", "Owner G", "loan-g@example.com")

    resp = await client.post(
        "/api/v1/turbo/loans/quote",
        json={
            "product_code": "motorcycle",
            "requested_amount": "5000",
            "collateral_value": "50000",
            "term_months": 48,  # motorcycle's max_term_months is 36
        },
        headers=headers,
    )
    assert resp.status_code == 400


async def test_apply_creates_submitted_application_matching_quote(client):
    headers = await _make_tier_1_tenant(client, "Shop H", "Owner H", "loan-h@example.com")

    resp = await client.post(
        "/api/v1/turbo/loans/applications",
        json={"product_code": "motorcycle", "requested_amount": "8000", "collateral_value": "20000", "term_months": 12},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "submitted"
    assert float(body["approved_amount"]) == 8000.0
    assert body["cap_reasons"] == []
    assert body["credit_tier_snapshot"] == "tier_1"

    listed = await client.get("/api/v1/turbo/loans/applications", headers=headers)
    assert len(listed.json()) == 1


async def test_disburse_creates_account_with_full_schedule(client):
    headers = await _make_tier_1_tenant(client, "Shop I", "Owner I", "loan-i@example.com")

    application = (
        await client.post(
            "/api/v1/turbo/loans/applications",
            json={
                "product_code": "motorcycle",
                "requested_amount": "8000",
                "collateral_value": "20000",
                "term_months": 12,
            },
            headers=headers,
        )
    ).json()

    resp = await client.post(
        f"/api/v1/turbo/loans/applications/{application['id']}/disburse", headers=headers
    )
    assert resp.status_code == 200, resp.text
    account = resp.json()
    assert account["status"] == "active"
    assert float(account["principal"]) == 8000.0
    assert account["account_number"].startswith("TB-")

    summary = (await client.get("/api/v1/turbo/loans/account", headers=headers)).json()
    assert summary["installments_total"] == 12
    assert summary["installments_paid"] == 0
    assert summary["on_time_payments"] == 0
    assert summary["has_overdue"] is False

    installments = (
        await client.get(f"/api/v1/turbo/loans/account/{account['id']}/installments", headers=headers)
    ).json()
    assert len(installments) == 12
    assert sum(Decimal(i["principal_component"]) for i in installments) == Decimal("8000.00")


async def test_cannot_disburse_same_application_twice(client):
    headers = await _make_tier_1_tenant(client, "Shop J", "Owner J", "loan-j@example.com")

    application = (
        await client.post(
            "/api/v1/turbo/loans/applications",
            json={"product_code": "motorcycle", "requested_amount": "5000", "collateral_value": "20000", "term_months": 12},
            headers=headers,
        )
    ).json()

    first = await client.post(f"/api/v1/turbo/loans/applications/{application['id']}/disburse", headers=headers)
    assert first.status_code == 200, first.text

    second = await client.post(f"/api/v1/turbo/loans/applications/{application['id']}/disburse", headers=headers)
    assert second.status_code == 400


async def test_cannot_disburse_second_loan_while_one_is_active(client):
    headers = await _make_tier_1_tenant(client, "Shop K", "Owner K", "loan-k@example.com")

    async def _apply_and_disburse():
        application = (
            await client.post(
                "/api/v1/turbo/loans/applications",
                json={
                    "product_code": "motorcycle",
                    "requested_amount": "3000",
                    "collateral_value": "20000",
                    "term_months": 12,
                },
                headers=headers,
            )
        ).json()
        return await client.post(f"/api/v1/turbo/loans/applications/{application['id']}/disburse", headers=headers)

    first = await _apply_and_disburse()
    assert first.status_code == 200, first.text

    second = await _apply_and_disburse()
    assert second.status_code == 400


async def test_pay_installment_marks_paid_and_rejects_double_payment(client):
    headers = await _make_tier_1_tenant(client, "Shop L", "Owner L", "loan-l@example.com")

    application = (
        await client.post(
            "/api/v1/turbo/loans/applications",
            json={"product_code": "motorcycle", "requested_amount": "5000", "collateral_value": "20000", "term_months": 12},
            headers=headers,
        )
    ).json()
    account = (
        await client.post(f"/api/v1/turbo/loans/applications/{application['id']}/disburse", headers=headers)
    ).json()
    installments = (
        await client.get(f"/api/v1/turbo/loans/account/{account['id']}/installments", headers=headers)
    ).json()
    first_installment = installments[0]

    pay_resp = await client.post(
        f"/api/v1/turbo/loans/installments/{first_installment['id']}/payment",
        json={"amount": first_installment["amount_due"], "reference": "test-ref"},
        headers=headers,
    )
    assert pay_resp.status_code == 200, pay_resp.text
    assert pay_resp.json()["status"] == "paid"

    dup = await client.post(
        f"/api/v1/turbo/loans/installments/{first_installment['id']}/payment",
        json={"amount": first_installment["amount_due"]},
        headers=headers,
    )
    assert dup.status_code == 400

    summary = (await client.get("/api/v1/turbo/loans/account", headers=headers)).json()
    assert summary["installments_paid"] == 1
    assert summary["on_time_payments"] == 1


async def test_pay_installment_rejects_underpayment(client):
    headers = await _make_tier_1_tenant(client, "Shop N", "Owner N", "loan-n@example.com")

    application = (
        await client.post(
            "/api/v1/turbo/loans/applications",
            json={"product_code": "motorcycle", "requested_amount": "5000", "collateral_value": "20000", "term_months": 12},
            headers=headers,
        )
    ).json()
    account = (
        await client.post(f"/api/v1/turbo/loans/applications/{application['id']}/disburse", headers=headers)
    ).json()
    installments = (
        await client.get(f"/api/v1/turbo/loans/account/{account['id']}/installments", headers=headers)
    ).json()
    first_installment = installments[0]

    resp = await client.post(
        f"/api/v1/turbo/loans/installments/{first_installment['id']}/payment",
        json={"amount": "1.00"},
        headers=headers,
    )
    assert resp.status_code == 400

    installments_after = (
        await client.get(f"/api/v1/turbo/loans/account/{account['id']}/installments", headers=headers)
    ).json()
    assert installments_after[0]["status"] == "unpaid"


async def test_account_closes_when_all_installments_paid(client):
    headers = await _make_tier_1_tenant(client, "Shop O", "Owner O", "loan-o@example.com")

    application = (
        await client.post(
            "/api/v1/turbo/loans/applications",
            json={"product_code": "motorcycle", "requested_amount": "3000", "collateral_value": "20000", "term_months": 6},
            headers=headers,
        )
    ).json()
    account = (
        await client.post(f"/api/v1/turbo/loans/applications/{application['id']}/disburse", headers=headers)
    ).json()
    installments = (
        await client.get(f"/api/v1/turbo/loans/account/{account['id']}/installments", headers=headers)
    ).json()

    for installment in installments:
        resp = await client.post(
            f"/api/v1/turbo/loans/installments/{installment['id']}/payment",
            json={"amount": installment["amount_due"]},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text

    summary = await client.get("/api/v1/turbo/loans/account", headers=headers)
    assert summary.json() is None


async def test_concurrent_disburse_only_creates_one_active_account(client, engine):
    headers = await _make_tier_1_tenant(client, "Shop P", "Owner P", "loan-p@example.com")

    async def _apply():
        resp = await client.post(
            "/api/v1/turbo/loans/applications",
            json={"product_code": "motorcycle", "requested_amount": "3000", "collateral_value": "20000", "term_months": 12},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]

    application_ids = [await _apply(), await _apply()]

    responses = await asyncio.gather(
        *(
            client.post(f"/api/v1/turbo/loans/applications/{app_id}/disburse", headers=headers)
            for app_id in application_ids
        )
    )
    assert sorted(r.status_code for r in responses) == [200, 400]

    session_factory = async_sessionmaker(engine)
    async with session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(LoanAccount))
    assert count == 1


async def test_cashier_cannot_access_loan_endpoints(client):
    tokens = await signup(client, "Shop M", "Owner M", "loan-m@example.com")
    owner_headers = auth_headers(tokens)
    me = await client.get("/api/v1/auth/me", headers=owner_headers)
    tenant_id = me.json()["tenant_id"]

    staff_resp = await client.post(
        "/api/v1/staff", json={"name": "Cashier M", "role": "cashier", "pin": "1234"}, headers=owner_headers
    )
    assert staff_resp.status_code == 201, staff_resp.text
    pin_login = await client.post("/api/v1/auth/pin-login", json={"tenant_id": tenant_id, "pin": "1234"})
    cashier_headers = auth_headers(pin_login.json())

    resp = await client.get("/api/v1/turbo/loans/products", headers=cashier_headers)
    assert resp.status_code == 403
