"""Seeds branch-side demo data for the TURBO case pitch: a Branch Champion
account, merchant prospects at different stages of the "Morning Route", and
Leads at different points in the 15-minute SLA — so the Branch Champion demo
(see BubusuperPOS_Frontend's /branch screens) has something to show the
moment the app is opened, same rationale as seed_demo.py on the shop side.
Safe to re-run: matches existing rows by branch code / email and skips
recreating them.

Usage — this must run against the SAME database the app is actually serving
from (see seed_demo.py for the same caveat in detail):

    docker exec infra-backend-1 python scripts/seed_branch_demo.py
"""

import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.db import async_session_factory  # noqa: E402
from app.core.security import hash_secret_async  # noqa: E402
from app.models.turbo.branch import (  # noqa: E402
    Branch,
    Lead,
    LeadSource,
    LeadStatus,
    MerchantProspect,
    MerchantProspectStatus,
)
from app.models.user import User, UserRole  # noqa: E402

_now = lambda: datetime.now(timezone.utc)  # noqa: E731

_STAFF_EMAIL = "test2@test.com"
_STAFF_PASSWORD = "12345678"

_MAIN_BRANCH = {
    "code": "BKK-CENTRAL",
    "name": "สาขาสีลม",
    "province": "กรุงเทพ",
    "lat": Decimal("13.724800"),
    "lng": Decimal("100.534200"),
}
_RIVAL_BRANCH = {
    "code": "BKK-THONGLOR",
    "name": "สาขาทองหล่อ",
    "province": "กรุงเทพ",
    "lat": Decimal("13.730800"),
    "lng": Decimal("100.581100"),
}

_PROSPECTS = [
    {"name": "ร้านส้มตำป้าแดง", "business_type": "food", "phone": "0811111111", "status": MerchantProspectStatus.visited},
    {"name": "ร้านผลไม้ลุงมี", "business_type": "food", "phone": "0822222222", "status": MerchantProspectStatus.converted},
    {"name": "ร้านซ่อมมือถือน้องเบล", "business_type": "service", "phone": "0833333333", "status": MerchantProspectStatus.not_visited},
    {"name": "ร้านขายลอตเตอรี่ป้าน้อย", "business_type": "retail", "phone": "0844444444", "status": MerchantProspectStatus.not_interested},
    {"name": "ร้านก๋วยเตี๋ยวเจ๊แอน", "business_type": "food", "phone": "0855555555", "status": MerchantProspectStatus.not_visited},
    {"name": "ร้านเสื้อผ้ามือสอง", "business_type": "retail", "phone": "0866666666", "status": MerchantProspectStatus.visited},
]

# minutes_ago=None means "just created" (fresh SLA countdown just started).
_LEADS = [
    {"name": "แม่ค้าส้มตำ (จากเว็บ)", "occupation": "ขายส้มตำ", "age": 38, "source": LeadSource.o2o_web, "minutes_ago": None},
    {"name": "คนขับวินมอเตอร์ไซค์", "occupation": "วินมอเตอร์ไซค์", "age": 45, "source": LeadSource.o2o_web, "minutes_ago": 12},
    {"name": "แม่ค้าขายผลไม้", "occupation": "ขายผลไม้", "age": 33, "source": LeadSource.visit, "minutes_ago": 60 * 3, "responded_status": LeadStatus.contacted},
    {"name": "ร้านซักรีด", "occupation": "ซักรีด", "age": 29, "source": LeadSource.referral, "minutes_ago": 60 * 24, "responded_status": LeadStatus.converted},
    {"name": "ร้านขายของชำ", "occupation": "ขายของชำ", "age": 52, "source": LeadSource.visit, "minutes_ago": 60 * 5, "responded_status": LeadStatus.lost},
]


async def _get_or_create_branch(db, spec: dict) -> Branch:
    branch = await db.scalar(select(Branch).where(Branch.code == spec["code"]))
    if branch is None:
        branch = Branch(
            code=spec["code"], name=spec["name"], province=spec["province"], lat=spec["lat"], lng=spec["lng"]
        )
        db.add(branch)
        await db.flush()
    return branch


async def _get_or_create_staff(db, branch: Branch) -> User:
    staff = await db.scalar(select(User).where(User.email == _STAFF_EMAIL))
    if staff is None:
        staff = User(
            branch_id=branch.id,
            name="พนักงานทดสอบ",
            email=_STAFF_EMAIL,
            password_hash=await hash_secret_async(_STAFF_PASSWORD),
            role=UserRole.branch_champion,
        )
        db.add(staff)
        await db.flush()
    return staff


async def _seed_prospects(db, branch: Branch) -> None:
    existing = await db.scalar(select(MerchantProspect).where(MerchantProspect.branch_id == branch.id))
    if existing is not None:
        print(f"  (ข้าม) {branch.name} มีรายชื่อผู้ค้าอยู่แล้ว")
        return
    for p in _PROSPECTS:
        visited_fields = (
            {"last_visited_at": _now() - timedelta(hours=2), "note": "คุยแล้ว สนใจฟังเพิ่ม"}
            if p["status"] != MerchantProspectStatus.not_visited
            else {}
        )
        db.add(
            MerchantProspect(
                branch_id=branch.id,
                name=p["name"],
                business_type=p["business_type"],
                phone=p["phone"],
                status=p["status"],
                **visited_fields,
            )
        )


async def _seed_leads(db, branch: Branch) -> None:
    existing = await db.scalar(select(Lead).where(Lead.assigned_branch_id == branch.id))
    if existing is not None:
        print(f"  (ข้าม) {branch.name} มี Lead อยู่แล้ว")
        return
    for lead_spec in _LEADS:
        created_at = _now() if lead_spec["minutes_ago"] is None else _now() - timedelta(minutes=lead_spec["minutes_ago"])
        responded_status = lead_spec.get("responded_status")
        db.add(
            Lead(
                assigned_branch_id=branch.id,
                source=lead_spec["source"],
                name=lead_spec["name"],
                occupation=lead_spec["occupation"],
                age=lead_spec["age"],
                quoted_daily_benefit=Decimal("500.00"),
                quoted_premium=Decimal("1.75"),
                status=responded_status or LeadStatus.new,
                first_response_at=created_at + timedelta(minutes=5) if responded_status else None,
                created_at=created_at,
            )
        )


async def _seed_rival_branch_activity(db, branch: Branch) -> None:
    """A little activity on a second branch so the leaderboard tab has an
    actual ranking to show, not just one row."""
    existing = await db.scalar(select(MerchantProspect).where(MerchantProspect.branch_id == branch.id))
    if existing is not None:
        print(f"  (ข้าม) {branch.name} มีข้อมูลอยู่แล้ว")
        return
    db.add_all(
        [
            MerchantProspect(
                branch_id=branch.id, name="ร้านหมูปิ้งป้าตุ๊ก", status=MerchantProspectStatus.visited,
                last_visited_at=_now() - timedelta(days=1),
            ),
            MerchantProspect(
                branch_id=branch.id, name="ร้านขายเสื้อผ้าเด็ก", status=MerchantProspectStatus.visited,
                last_visited_at=_now() - timedelta(days=2),
            ),
        ]
    )
    db.add(
        Lead(
            assigned_branch_id=branch.id,
            source=LeadSource.visit,
            name="ร้านขายของฝาก",
            occupation="ขายของฝาก",
            age=41,
            status=LeadStatus.contacted,
            first_response_at=_now() - timedelta(days=1),
            created_at=_now() - timedelta(days=1, minutes=10),
        )
    )


async def main() -> None:
    async with async_session_factory() as db:
        main_branch = await _get_or_create_branch(db, _MAIN_BRANCH)
        rival_branch = await _get_or_create_branch(db, _RIVAL_BRANCH)
        staff = await _get_or_create_staff(db, main_branch)
        await _seed_prospects(db, main_branch)
        await _seed_leads(db, main_branch)
        await _seed_rival_branch_activity(db, rival_branch)
        await db.commit()

    print()
    print("เสร็จแล้วครับ — ล็อกอินพนักงานสาขาด้วย:")
    print(f"  อีเมล:     {_STAFF_EMAIL}")
    print(f"  รหัสผ่าน:  {_STAFF_PASSWORD}")
    print(f"  สาขา:      {_MAIN_BRANCH['name']} ({_MAIN_BRANCH['code']})")
    print(f"  เพิ่มสาขาคู่แข่งไว้เทียบ leaderboard: {_RIVAL_BRANCH['name']}")


if __name__ == "__main__":
    asyncio.run(main())
