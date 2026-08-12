import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.turbo.branch import Lead, LeadSource, MerchantProspect, ProspectContactStatus
from app.models.turbo.loan import LoanApplication, LoanApplicationStatus, LoanCollateralKind, LoanProduct

from .conftest import auth_headers, signup


async def _seed_loan_application(engine, tenant_id, branch_id, status=LoanApplicationStatus.submitted):
    """Inserted directly rather than walked through quote()/apply() — these
    tests are about branch-scope isolation on the review endpoints, not
    eligibility/quoting mechanics (already covered in test_turbo_loan.py),
    and going through the real flow here would also need a 30-day sales
    streak fixture this file doesn't otherwise need."""
    session_factory = async_sessionmaker(engine)
    async with session_factory() as session:
        product = LoanProduct(
            code=f"motorcycle-{uuid.uuid4().hex[:8]}",
            collateral_kind=LoanCollateralKind.motorcycle,
            name="สินเชื่อรถมอเตอร์ไซค์",
            description="ทดสอบ",
            max_principal=Decimal("100000"),
            monthly_interest_rate=Decimal("0.02"),
        )
        session.add(product)
        await session.flush()
        application = LoanApplication(
            tenant_id=uuid.UUID(tenant_id),
            product_id=product.id,
            requested_amount=Decimal("5000"),
            collateral_value=Decimal("20000"),
            term_months=12,
            approved_amount=Decimal("5000"),
            monthly_installment=Decimal("450"),
            monthly_interest_rate_snapshot=Decimal("0.02"),
            income_profile_snapshot={},
            credit_tier_snapshot="tier_1",
            assigned_branch_id=uuid.UUID(branch_id),
            status=status,
        )
        session.add(application)
        await session.flush()
        application_id = str(application.id)
        await session.commit()
    return application_id


async def _branch_signup(
    client, branch_code, email, staff_name="Champion", province="กรุงเทพ", lat=None, lng=None
):
    resp = await client.post(
        "/api/v1/turbo/branch/signup",
        json={
            "branch_code": branch_code,
            "branch_name": f"สาขา {branch_code}",
            "province": province,
            "staff_name": staff_name,
            "email": email,
            "password": "Password123!",
            "lat": lat,
            "lng": lng,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_branch_signup_creates_branch_and_champion(client):
    tokens = await _branch_signup(client, "BKK-001", "champion-a@example.com")
    headers = auth_headers(tokens)

    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["role"] == "branch_champion"
    assert body["branch_id"] is not None
    assert body["tenant_id"] is None


async def test_second_signup_with_same_code_joins_existing_branch(client):
    tokens_a = await _branch_signup(client, "BKK-002", "champion-b1@example.com")
    tokens_b = await _branch_signup(client, "BKK-002", "champion-b2@example.com", staff_name="Champion 2")

    me_a = (await client.get("/api/v1/auth/me", headers=auth_headers(tokens_a))).json()
    me_b = (await client.get("/api/v1/auth/me", headers=auth_headers(tokens_b))).json()
    assert me_a["branch_id"] == me_b["branch_id"]


async def test_branch_champion_cannot_access_tenant_endpoints(client):
    tokens = await _branch_signup(client, "BKK-003", "champion-c@example.com")
    resp = await client.get("/api/v1/products", headers=auth_headers(tokens))
    assert resp.status_code == 403


async def test_shop_owner_cannot_access_branch_endpoints(client):
    signup_resp = await client.post(
        "/api/v1/auth/signup",
        json={
            "business_name": "Shop D",
            "owner_name": "Owner D",
            "email": "owner-d@example.com",
            "password": "Password123!",
        },
    )
    assert signup_resp.status_code == 201
    resp = await client.get("/api/v1/turbo/branch/prospects", headers=auth_headers(signup_resp.json()))
    assert resp.status_code == 403


async def test_branch_champion_can_login_via_shared_login_endpoint(client):
    """/auth/login is shared with ordinary shop accounts — a branch account
    doesn't need a separate login endpoint, only a separate signup."""
    await _branch_signup(client, "BKK-004", "champion-e@example.com")

    resp = await client.post(
        "/api/v1/auth/login", json={"email": "champion-e@example.com", "password": "Password123!"}
    )
    assert resp.status_code == 200, resp.text
    me = await client.get("/api/v1/auth/me", headers=auth_headers(resp.json()))
    assert me.json()["role"] == "branch_champion"


async def test_create_and_list_prospects(client):
    tokens = await _branch_signup(client, "BKK-005", "champion-f@example.com")
    headers = auth_headers(tokens)

    resp = await client.post(
        "/api/v1/turbo/branch/prospects",
        json={"name": "ร้านส้มตำป้าแดง", "business_type": "food", "phone": "0811111111"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "not_visited"

    listed = await client.get("/api/v1/turbo/branch/prospects", headers=headers)
    assert len(listed.json()) == 1
    assert listed.json()[0]["name"] == "ร้านส้มตำป้าแดง"


async def test_create_prospect_ignores_contact_status_in_the_request(client):
    """A prospect always starts not_scheduled regardless of what's sent — see
    ProspectCreateRequest's comment. Letting the caller set called/met at
    creation would let the leaderboard be gamed with backdated activity."""
    tokens = await _branch_signup(client, "BKK-024", "champion-y@example.com")
    headers = auth_headers(tokens)

    resp = await client.post(
        "/api/v1/turbo/branch/prospects",
        json={"name": "ร้านลัด", "contact_status": "met"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["contact_status"] == "not_scheduled"
    assert body["called_at"] is None
    assert body["met_at"] is None
    assert body["contact_status_updated_at"] is None


async def test_update_prospect_application_interest(client):
    tokens = await _branch_signup(client, "BKK-025", "champion-z@example.com")
    headers = auth_headers(tokens)
    prospect = (
        await client.post("/api/v1/turbo/branch/prospects", json={"name": "ร้านผลไม้"}, headers=headers)
    ).json()
    assert prospect["application_interest"] == "not_applied"

    resp = await client.post(
        f"/api/v1/turbo/branch/prospects/{prospect['id']}/application-interest",
        json={"application_interest": "applied_both"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["application_interest"] == "applied_both"


async def test_update_prospect_application_interest_from_other_branch_is_404(client):
    tokens_a = await _branch_signup(client, "BKK-026", "champion-aa@example.com")
    tokens_b = await _branch_signup(client, "BKK-027", "champion-bb@example.com")

    prospect = (
        await client.post(
            "/api/v1/turbo/branch/prospects", json={"name": "ร้าน A"}, headers=auth_headers(tokens_a)
        )
    ).json()

    resp = await client.post(
        f"/api/v1/turbo/branch/prospects/{prospect['id']}/application-interest",
        json={"application_interest": "applied_loan"},
        headers=auth_headers(tokens_b),
    )
    assert resp.status_code == 404


async def test_leaderboard_score_includes_prospects_contacted(client):
    tokens = await _branch_signup(client, "BKK-028", "champion-cc@example.com")
    headers = auth_headers(tokens)
    prospect = (
        await client.post("/api/v1/turbo/branch/prospects", json={"name": "ร้านโทร"}, headers=headers)
    ).json()
    await client.post(
        f"/api/v1/turbo/branch/prospects/{prospect['id']}/contact-status",
        json={"contact_status": "called"},
        headers=headers,
    )

    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    resp = await client.get("/api/v1/turbo/branch/leaderboard", headers=headers)
    row = next(r for r in resp.json() if r["branch_id"] == me["branch_id"])
    assert row["prospects_contacted"] == 1
    assert row["score"] == 1


async def test_visit_prospect_updates_status_and_timestamp(client):
    tokens = await _branch_signup(client, "BKK-006", "champion-g@example.com")
    headers = auth_headers(tokens)
    prospect = (
        await client.post("/api/v1/turbo/branch/prospects", json={"name": "ร้านผลไม้"}, headers=headers)
    ).json()
    assert prospect["last_visited_at"] is None

    resp = await client.post(
        f"/api/v1/turbo/branch/prospects/{prospect['id']}/visit",
        json={"status": "visited", "note": "คุยแล้ว สนใจ"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "visited"
    assert body["note"] == "คุยแล้ว สนใจ"
    assert body["last_visited_at"] is not None


async def test_prospects_are_scoped_to_own_branch(client):
    tokens_a = await _branch_signup(client, "BKK-007", "champion-h@example.com")
    tokens_b = await _branch_signup(client, "BKK-008", "champion-i@example.com")

    await client.post("/api/v1/turbo/branch/prospects", json={"name": "ร้าน A"}, headers=auth_headers(tokens_a))

    listed_b = await client.get("/api/v1/turbo/branch/prospects", headers=auth_headers(tokens_b))
    assert listed_b.json() == []


async def test_visit_prospect_from_other_branch_is_404(client):
    tokens_a = await _branch_signup(client, "BKK-009", "champion-j@example.com")
    tokens_b = await _branch_signup(client, "BKK-010", "champion-k@example.com")

    prospect = (
        await client.post(
            "/api/v1/turbo/branch/prospects", json={"name": "ร้าน A"}, headers=auth_headers(tokens_a)
        )
    ).json()

    resp = await client.post(
        f"/api/v1/turbo/branch/prospects/{prospect['id']}/visit",
        json={"status": "visited"},
        headers=auth_headers(tokens_b),
    )
    assert resp.status_code == 404


async def test_update_prospect_contact_status(client):
    tokens = await _branch_signup(client, "BKK-015", "champion-p@example.com")
    headers = auth_headers(tokens)
    prospect = (
        await client.post("/api/v1/turbo/branch/prospects", json={"name": "ร้านผลไม้"}, headers=headers)
    ).json()
    assert prospect["contact_status"] == "not_scheduled"

    resp = await client.post(
        f"/api/v1/turbo/branch/prospects/{prospect['id']}/contact-status",
        json={"contact_status": "called"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["contact_status"] == "called"
    # Untouched by this call — contact_status is independent of the visit flow.
    assert resp.json()["status"] == "not_visited"


async def test_update_prospect_contact_status_from_other_branch_is_404(client):
    tokens_a = await _branch_signup(client, "BKK-016", "champion-q@example.com")
    tokens_b = await _branch_signup(client, "BKK-017", "champion-r@example.com")

    prospect = (
        await client.post(
            "/api/v1/turbo/branch/prospects", json={"name": "ร้าน A"}, headers=auth_headers(tokens_a)
        )
    ).json()

    resp = await client.post(
        f"/api/v1/turbo/branch/prospects/{prospect['id']}/contact-status",
        json={"contact_status": "called"},
        headers=auth_headers(tokens_b),
    )
    assert resp.status_code == 404


async def test_delete_prospect_removes_it(client):
    tokens = await _branch_signup(client, "BKK-018", "champion-s@example.com")
    headers = auth_headers(tokens)
    prospect = (
        await client.post("/api/v1/turbo/branch/prospects", json={"name": "ร้านผลไม้"}, headers=headers)
    ).json()

    resp = await client.delete(f"/api/v1/turbo/branch/prospects/{prospect['id']}", headers=headers)
    assert resp.status_code == 204, resp.text

    listed = await client.get("/api/v1/turbo/branch/prospects", headers=headers)
    assert listed.json() == []


async def test_delete_prospect_from_other_branch_is_404(client):
    tokens_a = await _branch_signup(client, "BKK-019", "champion-t@example.com")
    tokens_b = await _branch_signup(client, "BKK-020", "champion-u@example.com")

    prospect = (
        await client.post(
            "/api/v1/turbo/branch/prospects", json={"name": "ร้าน A"}, headers=auth_headers(tokens_a)
        )
    ).json()

    resp = await client.delete(
        f"/api/v1/turbo/branch/prospects/{prospect['id']}", headers=auth_headers(tokens_b)
    )
    assert resp.status_code == 404


async def _insert_lead(engine, branch_id, name="สนใจแล้ว", prospect_id=None):
    session_factory = async_sessionmaker(engine)
    lead_id = uuid.uuid4()
    async with session_factory() as session:
        session.add(
            Lead(
                id=lead_id,
                assigned_branch_id=branch_id,
                prospect_id=prospect_id,
                source=LeadSource.o2o_web,
                name=name,
                occupation="แม่ค้า",
                age=35,
            )
        )
        await session.commit()
    return lead_id


async def test_delete_prospect_with_associated_lead_is_409(client, engine):
    tokens = await _branch_signup(client, "BKK-021", "champion-v@example.com")
    headers = auth_headers(tokens)
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    prospect = (
        await client.post("/api/v1/turbo/branch/prospects", json={"name": "ร้านผลไม้"}, headers=headers)
    ).json()

    await _insert_lead(engine, me["branch_id"], prospect_id=uuid.UUID(prospect["id"]))

    resp = await client.delete(f"/api/v1/turbo/branch/prospects/{prospect['id']}", headers=headers)
    assert resp.status_code == 409

    listed = await client.get("/api/v1/turbo/branch/prospects", headers=headers)
    assert len(listed.json()) == 1


async def test_respond_to_lead_sets_first_response_once(client, engine):
    tokens = await _branch_signup(client, "BKK-011", "champion-l@example.com")
    headers = auth_headers(tokens)
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()

    lead_id = await _insert_lead(engine, me["branch_id"])

    first = await client.post(
        f"/api/v1/turbo/branch/leads/{lead_id}/respond", json={"status": "contacted"}, headers=headers
    )
    assert first.status_code == 200, first.text
    first_response_at = first.json()["first_response_at"]
    assert first_response_at is not None

    second = await client.post(
        f"/api/v1/turbo/branch/leads/{lead_id}/respond", json={"status": "converted"}, headers=headers
    )
    assert second.status_code == 200, second.text
    assert second.json()["first_response_at"] == first_response_at
    assert second.json()["status"] == "converted"

    listed = await client.get("/api/v1/turbo/branch/leads", headers=headers)
    assert len(listed.json()) == 1


async def test_leaderboard_ranks_branches_by_score(client, engine):
    tokens_a = await _branch_signup(client, "BKK-012", "champion-m@example.com")
    tokens_b = await _branch_signup(client, "BKK-013", "champion-n@example.com")
    me_a = (await client.get("/api/v1/auth/me", headers=auth_headers(tokens_a))).json()
    me_b = (await client.get("/api/v1/auth/me", headers=auth_headers(tokens_b))).json()

    # Branch A: 1 visited prospect. Branch B: 1 contacted lead (worth 2x).
    prospect = (
        await client.post(
            "/api/v1/turbo/branch/prospects", json={"name": "ร้าน A"}, headers=auth_headers(tokens_a)
        )
    ).json()
    await client.post(
        f"/api/v1/turbo/branch/prospects/{prospect['id']}/visit",
        json={"status": "visited"},
        headers=auth_headers(tokens_a),
    )

    lead_id = await _insert_lead(engine, me_b["branch_id"])
    await client.post(
        f"/api/v1/turbo/branch/leads/{lead_id}/respond", json={"status": "contacted"}, headers=auth_headers(tokens_b)
    )

    resp = await client.get("/api/v1/turbo/branch/leaderboard", headers=auth_headers(tokens_a))
    assert resp.status_code == 200, resp.text
    by_branch = {row["branch_id"]: row for row in resp.json()}
    assert by_branch[me_a["branch_id"]]["score"] == 1
    assert by_branch[me_b["branch_id"]]["score"] == 2
    scores = [row["score"] for row in resp.json()]
    assert scores == sorted(scores, reverse=True)


async def test_leaderboard_maps_called_to_contacted_and_met_to_visited(client):
    tokens = await _branch_signup(client, "BKK-022", "champion-w@example.com")
    headers = auth_headers(tokens)

    called = (
        await client.post("/api/v1/turbo/branch/prospects", json={"name": "ร้านโทร"}, headers=headers)
    ).json()
    await client.post(
        f"/api/v1/turbo/branch/prospects/{called['id']}/contact-status",
        json={"contact_status": "called"},
        headers=headers,
    )
    met = (await client.post("/api/v1/turbo/branch/prospects", json={"name": "ร้านนัดพบ"}, headers=headers)).json()
    await client.post(
        f"/api/v1/turbo/branch/prospects/{met['id']}/contact-status",
        json={"contact_status": "met"},
        headers=headers,
    )
    # Not contacted yet, and explicitly unreachable — neither should count.
    await client.post("/api/v1/turbo/branch/prospects", json={"name": "ร้านยังไม่ติดต่อ"}, headers=headers)
    unreachable = (
        await client.post("/api/v1/turbo/branch/prospects", json={"name": "ร้านติดต่อไม่ได้"}, headers=headers)
    ).json()
    await client.post(
        f"/api/v1/turbo/branch/prospects/{unreachable['id']}/contact-status",
        json={"contact_status": "unreachable"},
        headers=headers,
    )

    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    resp = await client.get("/api/v1/turbo/branch/leaderboard", headers=headers)
    row = next(r for r in resp.json() if r["branch_id"] == me["branch_id"])
    # Called counts as "contacted"; met counts as "visited" instead, not both.
    assert row["prospects_contacted"] == 1
    assert row["prospects_visited"] == 1


async def test_leaderboard_counts_are_cumulative_not_a_status_snapshot(client):
    """Changing a prospect's contact_status must not erase counts it already
    earned — called_at/met_at each persist independently once set (see the
    model's comment), unlike contact_status itself which only holds the
    latest value."""
    tokens = await _branch_signup(client, "BKK-023", "champion-x@example.com")
    headers = auth_headers(tokens)

    prospect = (
        await client.post("/api/v1/turbo/branch/prospects", json={"name": "ร้านทดสอบ"}, headers=headers)
    ).json()
    await client.post(
        f"/api/v1/turbo/branch/prospects/{prospect['id']}/contact-status",
        json={"contact_status": "called"},
        headers=headers,
    )
    # Now mark the same prospect met — current contact_status moves on, but
    # the earlier call should still count.
    await client.post(
        f"/api/v1/turbo/branch/prospects/{prospect['id']}/contact-status",
        json={"contact_status": "met"},
        headers=headers,
    )

    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    resp = await client.get("/api/v1/turbo/branch/leaderboard", headers=headers)
    row = next(r for r in resp.json() if r["branch_id"] == me["branch_id"])
    assert row["prospects_contacted"] == 1
    assert row["prospects_visited"] == 1


async def test_leaderboard_ignores_activity_outside_the_7_day_window(client, engine):
    tokens = await _branch_signup(client, "BKK-014", "champion-o@example.com")
    me = (await client.get("/api/v1/auth/me", headers=auth_headers(tokens))).json()

    session_factory = async_sessionmaker(engine)
    async with session_factory() as session:
        session.add(
            MerchantProspect(
                branch_id=me["branch_id"],
                name="ร้านเก่า",
                last_visited_at=datetime.now(timezone.utc) - timedelta(days=30),
                contact_status=ProspectContactStatus.met,
                contact_status_updated_at=datetime.now(timezone.utc) - timedelta(days=30),
                met_at=datetime.now(timezone.utc) - timedelta(days=30),
            )
        )
        await session.commit()

    resp = await client.get("/api/v1/turbo/branch/leaderboard", headers=auth_headers(tokens))
    row = next(r for r in resp.json() if r["branch_id"] == me["branch_id"])
    assert row["prospects_visited"] == 0
    assert row["prospects_contacted"] == 0


async def test_nearby_branches_are_sorted_by_distance(client):
    # Bangkok (13.7563, 100.5018) as the caller's own position — the "near"
    # branch is ~2km away, the "far" one ~400km away (Chiang Mai).
    await _branch_signup(client, "BKK-NEAR", "champion-near@example.com", lat="13.7300", lng="100.5200")
    await _branch_signup(client, "CNX-FAR", "champion-far@example.com", province="เชียงใหม่", lat="18.7883", lng="98.9853")

    tokens = await signup(client, "Shop Nearby", "Owner Nearby", "nearby-owner@example.com")
    headers = auth_headers(tokens)

    resp = await client.get(
        "/api/v1/turbo/branch/nearby", params={"lat": "13.7563", "lng": "100.5018"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    results = resp.json()
    assert results[0]["code"] == "BKK-NEAR"
    assert results[0]["distance_km"] < 10
    assert results[-1]["code"] == "CNX-FAR"
    assert results[-1]["distance_km"] > 300


async def test_branch_without_coordinates_is_excluded_from_nearby(client):
    await _branch_signup(client, "BKK-NOCOORDS", "champion-nocoords@example.com")

    tokens = await signup(client, "Shop NoCoords", "Owner NoCoords", "nocoords-owner@example.com")
    headers = auth_headers(tokens)

    resp = await client.get(
        "/api/v1/turbo/branch/nearby", params={"lat": "13.7563", "lng": "100.5018"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert "BKK-NOCOORDS" not in [b["code"] for b in resp.json()]


async def test_champion_sees_only_own_branch_loan_applications(client, engine):
    tokens_a = await _branch_signup(client, "LOAN-BR-01", "champion-loan-a@example.com")
    tokens_b = await _branch_signup(client, "LOAN-BR-02", "champion-loan-b@example.com")
    me_a = (await client.get("/api/v1/auth/me", headers=auth_headers(tokens_a))).json()

    shop = await signup(client, "Shop Loan A", "Owner Loan A", "shop-loan-a@example.com")
    shop_me = (await client.get("/api/v1/auth/me", headers=auth_headers(shop))).json()
    await _seed_loan_application(engine, shop_me["tenant_id"], me_a["branch_id"])

    listed_a = await client.get("/api/v1/turbo/branch/loan-applications", headers=auth_headers(tokens_a))
    assert len(listed_a.json()) == 1

    listed_b = await client.get("/api/v1/turbo/branch/loan-applications", headers=auth_headers(tokens_b))
    assert listed_b.json() == []


async def test_champion_cannot_advance_another_branchs_application(client, engine):
    tokens_a = await _branch_signup(client, "LOAN-BR-03", "champion-loan-c@example.com")
    tokens_b = await _branch_signup(client, "LOAN-BR-04", "champion-loan-d@example.com")
    me_a = (await client.get("/api/v1/auth/me", headers=auth_headers(tokens_a))).json()

    shop = await signup(client, "Shop Loan B", "Owner Loan B", "shop-loan-b@example.com")
    shop_me = (await client.get("/api/v1/auth/me", headers=auth_headers(shop))).json()
    application_id = await _seed_loan_application(engine, shop_me["tenant_id"], me_a["branch_id"])

    resp = await client.post(
        f"/api/v1/turbo/branch/loan-applications/{application_id}/advance",
        json={"to_status": "doc_review"},
        headers=auth_headers(tokens_b),
    )
    assert resp.status_code == 404


async def test_champion_cannot_skip_a_stage(client, engine):
    tokens = await _branch_signup(client, "LOAN-BR-05", "champion-loan-e@example.com")
    me = (await client.get("/api/v1/auth/me", headers=auth_headers(tokens))).json()
    shop = await signup(client, "Shop Loan C", "Owner Loan C", "shop-loan-c@example.com")
    shop_me = (await client.get("/api/v1/auth/me", headers=auth_headers(shop))).json()
    application_id = await _seed_loan_application(engine, shop_me["tenant_id"], me["branch_id"])

    resp = await client.post(
        f"/api/v1/turbo/branch/loan-applications/{application_id}/advance",
        json={"to_status": "under_review"},
        headers=auth_headers(tokens),
    )
    assert resp.status_code == 400


async def test_reject_requires_a_reason(client, engine):
    tokens = await _branch_signup(client, "LOAN-BR-06", "champion-loan-f@example.com")
    me = (await client.get("/api/v1/auth/me", headers=auth_headers(tokens))).json()
    shop = await signup(client, "Shop Loan D", "Owner Loan D", "shop-loan-d@example.com")
    shop_me = (await client.get("/api/v1/auth/me", headers=auth_headers(shop))).json()
    application_id = await _seed_loan_application(engine, shop_me["tenant_id"], me["branch_id"])

    resp = await client.post(
        f"/api/v1/turbo/branch/loan-applications/{application_id}/reject",
        json={"reason": "abc"},
        headers=auth_headers(tokens),
    )
    assert resp.status_code == 422


async def test_reject_records_the_reason_and_blocks_further_transitions(client, engine):
    tokens = await _branch_signup(client, "LOAN-BR-07", "champion-loan-g@example.com")
    me = (await client.get("/api/v1/auth/me", headers=auth_headers(tokens))).json()
    shop = await signup(client, "Shop Loan E", "Owner Loan E", "shop-loan-e@example.com")
    shop_me = (await client.get("/api/v1/auth/me", headers=auth_headers(shop))).json()
    application_id = await _seed_loan_application(engine, shop_me["tenant_id"], me["branch_id"])

    resp = await client.post(
        f"/api/v1/turbo/branch/loan-applications/{application_id}/reject",
        json={"reason": "เอกสารไม่ครบถ้วนตามที่กำหนด"},
        headers=auth_headers(tokens),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "rejected"

    detail = await client.get(
        f"/api/v1/turbo/branch/loan-applications/{application_id}", headers=auth_headers(tokens)
    )
    assert detail.json()["events"][-1]["note"] == "เอกสารไม่ครบถ้วนตามที่กำหนด"

    again = await client.post(
        f"/api/v1/turbo/branch/loan-applications/{application_id}/advance",
        json={"to_status": "doc_review"},
        headers=auth_headers(tokens),
    )
    assert again.status_code == 400


async def test_shop_owner_cannot_call_branch_loan_review_endpoints(client):
    shop = await signup(client, "Shop Loan F", "Owner Loan F", "shop-loan-f@example.com")
    resp = await client.get("/api/v1/turbo/branch/loan-applications", headers=auth_headers(shop))
    assert resp.status_code == 403


async def test_champion_cannot_disburse(client):
    tokens = await _branch_signup(client, "LOAN-BR-08", "champion-loan-h@example.com")
    resp = await client.post(
        f"/api/v1/turbo/loans/applications/{uuid.uuid4()}/disburse", headers=auth_headers(tokens)
    )
    assert resp.status_code == 403


async def test_advance_writes_an_event_with_the_champion_as_actor(client, engine):
    tokens = await _branch_signup(client, "LOAN-BR-09", "champion-loan-i@example.com", staff_name="Champion Nine")
    me = (await client.get("/api/v1/auth/me", headers=auth_headers(tokens))).json()
    shop = await signup(client, "Shop Loan G", "Owner Loan G", "shop-loan-g@example.com")
    shop_me = (await client.get("/api/v1/auth/me", headers=auth_headers(shop))).json()
    application_id = await _seed_loan_application(engine, shop_me["tenant_id"], me["branch_id"])

    resp = await client.post(
        f"/api/v1/turbo/branch/loan-applications/{application_id}/advance",
        json={"to_status": "doc_review", "note": "เอกสารครบ"},
        headers=auth_headers(tokens),
    )
    assert resp.status_code == 200, resp.text
    last_event = resp.json()["events"][-1]
    assert last_event["actor_kind"] == "champion"
    assert last_event["to_status"] == "doc_review"
    assert last_event["note"] == "เอกสารครบ"


async def test_champion_action_appears_in_the_shop_owners_audit_log(client, engine):
    """The strongest evidence record_external actually works: a Champion
    (BranchContext, no tenant_id of their own) advances a tenant's
    application, and the tenant owner sees it in their own audit log."""
    tokens = await _branch_signup(client, "LOAN-BR-10", "champion-loan-j@example.com")
    me = (await client.get("/api/v1/auth/me", headers=auth_headers(tokens))).json()
    shop = await signup(client, "Shop Loan H", "Owner Loan H", "shop-loan-h@example.com")
    owner_headers = auth_headers(shop)
    shop_me = (await client.get("/api/v1/auth/me", headers=owner_headers)).json()
    application_id = await _seed_loan_application(engine, shop_me["tenant_id"], me["branch_id"])

    resp = await client.post(
        f"/api/v1/turbo/branch/loan-applications/{application_id}/advance",
        json={"to_status": "doc_review"},
        headers=auth_headers(tokens),
    )
    assert resp.status_code == 200, resp.text

    audit = await client.get("/api/v1/audit-log", headers=owner_headers)
    assert audit.status_code == 200, audit.text
    actions = [row["action"] for row in audit.json()]
    assert "loan.review_advance" in actions
