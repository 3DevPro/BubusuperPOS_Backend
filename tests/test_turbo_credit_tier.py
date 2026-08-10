import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.turbo.loan import (
    LoanAccount,
    LoanAccountStatus,
    LoanApplication,
    LoanApplicationStatus,
    LoanCollateralKind,
    LoanInstallment,
    LoanInstallmentStatus,
    LoanProduct,
)
from app.services.turbo.credit_service import is_on_time, resolve_tier

from .conftest import auth_headers, signup

_TZ = ZoneInfo("Asia/Bangkok")
_PRODUCT_ID = uuid.UUID("22222222-2222-4222-8222-222222222201")


def _today():
    return datetime.now(_TZ).date()


async def _close(client, headers, business_date, reason="open"):
    resp = await client.post(
        "/api/v1/turbo/daily-close",
        json={"business_date": str(business_date), "closed_reason": reason},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text


async def _make_30_day_streak(client, headers):
    for offset in range(30):
        await _close(client, headers, _today() - timedelta(days=offset))


async def _insert_paid_installments(engine, tenant_id, count, *, late=False):
    """Directly writes a LoanAccount and `count` paid LoanInstallment rows —
    bypassing the API's disburse/pay flow (already covered by
    test_turbo_loan.py) so this file can isolate resolve_tier's own
    threshold behavior. `late` pays every installment
    LOAN_LATE_GRACE_DAYS+1 days after due_date, so none of them should
    count as on-time."""
    from app.core.turbo_config import LOAN_LATE_GRACE_DAYS

    session_factory = async_sessionmaker(engine)
    async with session_factory() as session:
        session.add(
            LoanProduct(
                id=_PRODUCT_ID,
                code="motorcycle",
                collateral_kind=LoanCollateralKind.motorcycle,
                name="สินเชื่อรถมอเตอร์ไซค์",
                description="test",
                max_principal=Decimal("100000"),
                monthly_interest_rate=Decimal("0.02"),
            )
        )
        # No relationship() links these models (see app/models/base.py) —
        # SQLAlchemy can't infer insert order across mapper classes from raw
        # FK columns alone, so each parent needs an explicit flush before its
        # child is added, same convention seed_demo.py's tenant/owner uses.
        await session.flush()
        application_id = uuid.uuid4()
        session.add(
            LoanApplication(
                id=application_id,
                tenant_id=uuid.UUID(tenant_id),
                product_id=_PRODUCT_ID,
                requested_amount=Decimal("10000"),
                collateral_value=Decimal("20000"),
                term_months=12,
                approved_amount=Decimal("10000"),
                monthly_installment=Decimal("945.60"),
                monthly_interest_rate_snapshot=Decimal("0.02"),
                income_profile_snapshot={},
                credit_tier_snapshot="tier_1",
                cap_reasons=[],
                status=LoanApplicationStatus.disbursed,
            )
        )
        await session.flush()
        account_id = uuid.uuid4()
        session.add(
            LoanAccount(
                id=account_id,
                tenant_id=uuid.UUID(tenant_id),
                application_id=application_id,
                product_id=_PRODUCT_ID,
                account_number=f"TB-TEST-{uuid.uuid4().hex[:8]}",
                principal=Decimal("10000"),
                monthly_interest_rate=Decimal("0.02"),
                term_months=12,
                monthly_installment=Decimal("945.60"),
                status=LoanAccountStatus.active,
                first_due_date=_today(),
            )
        )
        for seq in range(1, count + 1):
            due_date = _today() - timedelta(days=30 * (count - seq + 1))
            # +2 days of slack past the grace window rather than +1 — Postgres
            # round-trips DateTime(timezone=True) as UTC, and a Bangkok
            # midnight (_TZ, UTC+7) converts to the *previous* UTC calendar
            # day, which would silently erase exactly one day of lateness at
            # the +1 boundary. Building this in UTC directly at noon (see
            # below) sidesteps the conversion, but the extra day of margin
            # keeps the assertion robust either way.
            lateness = timedelta(days=LOAN_LATE_GRACE_DAYS + 2) if late else timedelta(days=0)
            session.add(
                LoanInstallment(
                    tenant_id=uuid.UUID(tenant_id),
                    account_id=account_id,
                    sequence=seq,
                    due_date=due_date,
                    principal_component=Decimal("745.60"),
                    interest_component=Decimal("200.00"),
                    amount_due=Decimal("945.60"),
                    status=LoanInstallmentStatus.paid,
                    paid_at=datetime(due_date.year, due_date.month, due_date.day, 12, tzinfo=timezone.utc)
                    + lateness,
                    paid_amount=Decimal("945.60"),
                    paid_reference="test",
                )
            )
        await session.commit()


def test_resolve_tier_pure_thresholds():
    assert resolve_tier(streak_days=0, on_time_payments=0)[0] == "none"
    assert resolve_tier(streak_days=29, on_time_payments=99)[0] == "none"

    tier, limit, next_days, requirement = resolve_tier(streak_days=30, on_time_payments=0)
    assert tier == "tier_1"
    assert limit == Decimal("10000")
    assert next_days is None
    assert requirement is not None

    tier, limit, _, requirement = resolve_tier(streak_days=30, on_time_payments=3)
    assert tier == "tier_2"
    assert limit == Decimal("30000")

    tier, limit, _, requirement = resolve_tier(streak_days=30, on_time_payments=6)
    assert tier == "tier_3"
    assert limit == Decimal("50000")
    assert requirement is None  # top tier — nothing left to unlock

    # A streak short of 30 always wins over payment history — see the
    # case's "verify sales history first" ordering in credit_service.
    tier, _, next_days, _ = resolve_tier(streak_days=10, on_time_payments=10)
    assert tier == "none"
    assert next_days == 20


def test_is_on_time_converts_to_tenant_local_date_before_comparing():
    due_date = date(2026, 1, 1)

    # Bangkok is UTC+7: 2026-01-04 20:00 UTC is already 2026-01-05 03:00 in
    # Bangkok — one day beyond a 3-day grace window. The old code compared
    # paid_at.date() (the raw UTC date, still Jan 4 — inside the window) and
    # would have wrongly scored this payment as on time.
    late_paid_at = datetime(2026, 1, 4, 20, 0, tzinfo=timezone.utc)
    assert is_on_time(late_paid_at, due_date, _TZ, grace_days=3) is False

    # One UTC hour earlier is still Jan 4 in Bangkok too — within the window
    # under both the old and new logic.
    on_time_paid_at = datetime(2026, 1, 4, 16, 0, tzinfo=timezone.utc)
    assert is_on_time(on_time_paid_at, due_date, _TZ, grace_days=3) is True


async def test_credit_tier_stays_tier_1_below_tier_2_threshold(client, engine):
    tokens = await signup(client, "Shop A", "Owner A", "tier-a@example.com")
    headers = auth_headers(tokens)
    me = await client.get("/api/v1/auth/me", headers=headers)
    tenant_id = me.json()["tenant_id"]

    await _make_30_day_streak(client, headers)
    await _insert_paid_installments(engine, tenant_id, count=2)  # below TIER_2_ON_TIME_PAYMENTS (3)

    resp = await client.get("/api/v1/turbo/income-profile", headers=headers)
    body = resp.json()
    assert body["credit_tier"] == "tier_1"
    assert float(body["credit_limit"]) == 10000.0
    assert body["on_time_payments"] == 2


async def test_credit_tier_upgrades_to_tier_2_at_threshold(client, engine):
    tokens = await signup(client, "Shop B", "Owner B", "tier-b@example.com")
    headers = auth_headers(tokens)
    me = await client.get("/api/v1/auth/me", headers=headers)
    tenant_id = me.json()["tenant_id"]

    await _make_30_day_streak(client, headers)
    await _insert_paid_installments(engine, tenant_id, count=3)  # == TIER_2_ON_TIME_PAYMENTS

    resp = await client.get("/api/v1/turbo/income-profile", headers=headers)
    body = resp.json()
    assert body["credit_tier"] == "tier_2"
    assert float(body["credit_limit"]) == 30000.0


async def test_credit_tier_upgrades_to_tier_3_at_threshold(client, engine):
    tokens = await signup(client, "Shop C", "Owner C", "tier-c@example.com")
    headers = auth_headers(tokens)
    me = await client.get("/api/v1/auth/me", headers=headers)
    tenant_id = me.json()["tenant_id"]

    await _make_30_day_streak(client, headers)
    await _insert_paid_installments(engine, tenant_id, count=6)  # == TIER_3_ON_TIME_PAYMENTS

    resp = await client.get("/api/v1/turbo/income-profile", headers=headers)
    body = resp.json()
    assert body["credit_tier"] == "tier_3"
    assert float(body["credit_limit"]) == 50000.0


async def test_late_payments_do_not_count_toward_tier_progress(client, engine):
    tokens = await signup(client, "Shop D", "Owner D", "tier-d@example.com")
    headers = auth_headers(tokens)
    me = await client.get("/api/v1/auth/me", headers=headers)
    tenant_id = me.json()["tenant_id"]

    await _make_30_day_streak(client, headers)
    await _insert_paid_installments(engine, tenant_id, count=3, late=True)

    resp = await client.get("/api/v1/turbo/income-profile", headers=headers)
    body = resp.json()
    assert body["on_time_payments"] == 0
    assert body["credit_tier"] == "tier_1"
