import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.turbo.branch import Branch

from .conftest import auth_headers


async def _insert_branch(engine, code, province):
    session_factory = async_sessionmaker(engine)
    branch_id = uuid.uuid4()
    async with session_factory() as session:
        session.add(Branch(id=branch_id, code=code, name=f"สาขา {code}", province=province))
        await session.commit()
    return branch_id


async def test_quote_requires_no_auth(client, engine):
    await _insert_branch(engine, "PUB-001", "กรุงเทพ")
    resp = await client.post(
        "/api/v1/turbo/public/quote",
        json={"name": "แม่ค้า A", "occupation": "ขายส้มตำ", "age": 40, "monthly_budget": "300"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert float(body["daily_benefit"]) > 0
    assert float(body["premium_amount"]) > 0


async def test_quote_computes_benefit_from_monthly_budget(client, engine):
    await _insert_branch(engine, "PUB-002", "กรุงเทพ")
    resp = await client.post(
        "/api/v1/turbo/public/quote",
        json={"name": "แม่ค้า B", "occupation": "ขายผลไม้", "age": 30, "monthly_budget": "105"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # 105/30 = 3.5/day premium; benefit = premium / 0.0035 = 1000
    assert float(body["premium_amount"]) == 3.5
    assert float(body["daily_benefit"]) == 1000.0


async def test_quote_routes_lead_to_matching_province_branch(client, engine):
    await _insert_branch(engine, "PUB-003", "เชียงใหม่")
    await _insert_branch(engine, "PUB-004", "ภูเก็ต")

    resp = await client.post(
        "/api/v1/turbo/public/quote",
        json={
            "name": "แม่ค้า C",
            "occupation": "ขายไก่ทอด",
            "age": 28,
            "monthly_budget": "150",
            "province": "ภูเก็ต",
        },
    )
    assert resp.status_code == 201, resp.text
    lead_id = resp.json()["lead_id"]

    champ_phuket = await client.post(
        "/api/v1/turbo/branch/signup",
        json={
            "branch_code": "PUB-004",
            "branch_name": "x",
            "province": "ภูเก็ต",
            "staff_name": "c1",
            "email": "champ-phuket@example.com",
            "password": "Password123!",
        },
    )
    assert champ_phuket.status_code == 201, champ_phuket.text
    leads = (await client.get("/api/v1/turbo/branch/leads", headers=auth_headers(champ_phuket.json()))).json()
    assert any(lead["id"] == lead_id for lead in leads)


async def test_quote_falls_back_to_any_branch_without_province_match(client, engine):
    await _insert_branch(engine, "PUB-005", "กรุงเทพ")
    resp = await client.post(
        "/api/v1/turbo/public/quote",
        json={
            "name": "แม่ค้า D",
            "occupation": "ขายน้ำ",
            "age": 45,
            "monthly_budget": "60",
            "province": "จังหวัดที่ไม่มีสาขา",
        },
    )
    assert resp.status_code == 201, resp.text


async def test_quote_503_when_no_branches_exist(client):
    resp = await client.post(
        "/api/v1/turbo/public/quote",
        json={"name": "แม่ค้า E", "occupation": "ขายผัก", "age": 50, "monthly_budget": "90"},
    )
    assert resp.status_code == 503


async def test_quote_rate_limited_after_max_calls(client, engine):
    await _insert_branch(engine, "PUB-006", "กรุงเทพ")
    payload = {"name": "สแปม", "occupation": "x", "age": 20, "monthly_budget": "10"}

    for _ in range(8):
        resp = await client.post("/api/v1/turbo/public/quote", json=payload)
        assert resp.status_code == 201, resp.text

    blocked = await client.post("/api/v1/turbo/public/quote", json=payload)
    assert blocked.status_code == 429
