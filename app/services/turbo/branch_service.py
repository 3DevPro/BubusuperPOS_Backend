import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.branch_scope import BranchContext
from app.core.security import hash_secret_async
from app.models.turbo.branch import Branch, Lead, MerchantProspect, ProspectContactStatus
from app.models.user import User, UserRole
from app.schemas.turbo.branch import (
    BranchSignupRequest,
    LeaderboardEntryResponse,
    LeadRespondRequest,
    ProspectApplicationInterestUpdateRequest,
    ProspectContactStatusUpdateRequest,
    NearbyBranchResponse,
    ProspectCreateRequest,
    ProspectVisitRequest,
)

_EARTH_RADIUS_KM = 6371.0


def _haversine_km(lat1: Decimal, lng1: Decimal, lat2: Decimal, lng2: Decimal) -> float:
    lat1_r, lng1_r, lat2_r, lng2_r = (math.radians(float(v)) for v in (lat1, lng1, lat2, lng2))
    dlat = lat2_r - lat1_r
    dlng = lng2_r - lng1_r
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlng / 2) ** 2
    return _EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(a))

_LEADERBOARD_WINDOW = timedelta(days=7)


async def pick_branch_for_province(db: AsyncSession, province: str | None) -> Branch:
    """Routes a lead to a branch in the given province, or any branch if
    none is given/matched — used by both the public O2O quote form and the
    in-app loan application flow so a prospect/tenant always lands somewhere
    a Champion can follow up."""
    if province:
        branch = await db.scalar(
            select(Branch).where(Branch.province == province).order_by(func.random()).limit(1)
        )
        if branch is not None:
            return branch
    branch = await db.scalar(select(Branch).order_by(func.random()).limit(1))
    if branch is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "no branches available to route this lead to")
    return branch


async def signup(db: AsyncSession, body: BranchSignupRequest) -> User:
    """Joins an existing branch (matched by code) if one exists, otherwise
    creates it — a second Champion signing up with the same branch_code is
    the normal case (a branch has more than one Champion), not an error."""
    branch = await db.scalar(select(Branch).where(Branch.code == body.branch_code))
    if branch is None:
        branch = Branch(
            code=body.branch_code, name=body.branch_name, province=body.province, lat=body.lat, lng=body.lng
        )
        db.add(branch)
        await db.flush()

    staff = User(
        branch_id=branch.id,
        name=body.staff_name,
        email=body.email,
        password_hash=await hash_secret_async(body.password),
        role=UserRole.branch_champion,
    )
    db.add(staff)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered") from exc

    return staff


async def list_prospects(ctx: BranchContext) -> list[MerchantProspect]:
    result = await ctx.db.scalars(ctx.scoped(MerchantProspect).order_by(MerchantProspect.created_at.desc()))
    return list(result)


async def create_prospect(ctx: BranchContext, body: ProspectCreateRequest) -> MerchantProspect:
    # contact_status always starts not_scheduled (the model default) — see
    # ProspectCreateRequest's comment for why it can't be set here.
    prospect = MerchantProspect(
        branch_id=ctx.branch_id,
        name=body.name,
        business_type=body.business_type,
        address=body.address,
        phone=body.phone,
        application_interest=body.application_interest,
    )
    ctx.db.add(prospect)
    await ctx.db.commit()
    await ctx.db.refresh(prospect)
    return prospect


async def _get_prospect_or_404(ctx: BranchContext, prospect_id) -> MerchantProspect:
    prospect = await ctx.db.scalar(ctx.scoped(MerchantProspect).where(MerchantProspect.id == prospect_id))
    if prospect is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "prospect not found")
    return prospect


async def visit_prospect(ctx: BranchContext, prospect_id, body: ProspectVisitRequest) -> MerchantProspect:
    prospect = await _get_prospect_or_404(ctx, prospect_id)

    prospect.status = body.status
    prospect.note = body.note
    prospect.last_visited_at = datetime.now(timezone.utc)
    await ctx.db.commit()
    await ctx.db.refresh(prospect)
    return prospect


async def delete_prospect(ctx: BranchContext, prospect_id) -> None:
    prospect = await _get_prospect_or_404(ctx, prospect_id)

    await ctx.db.delete(prospect)
    try:
        await ctx.db.commit()
    except IntegrityError as exc:
        # A Lead can point back at this prospect (Lead.prospect_id) — no
        # ON DELETE behavior is set on that FK, so deleting under it would
        # orphan the lead's history rather than silently detaching it.
        await ctx.db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "cannot delete a prospect that already has an associated lead"
        ) from exc


async def update_prospect_contact_status(
    ctx: BranchContext, prospect_id, body: ProspectContactStatusUpdateRequest
) -> MerchantProspect:
    # Separate from visit_prospect on purpose — contact history (call/met/
    # unreachable) is logged the moment a Champion taps it, with no note or
    # visit-outcome bundled in, unlike the Morning Route visit flow above.
    prospect = await _get_prospect_or_404(ctx, prospect_id)

    now = datetime.now(timezone.utc)
    prospect.contact_status = body.contact_status
    prospect.contact_status_updated_at = now
    # called_at/met_at only ever move forward, never get cleared by a later
    # status change — see the model's comment for why (leaderboard history).
    if body.contact_status == ProspectContactStatus.called:
        prospect.called_at = now
    elif body.contact_status == ProspectContactStatus.met:
        prospect.met_at = now
    await ctx.db.commit()
    await ctx.db.refresh(prospect)
    return prospect


async def update_prospect_application_interest(
    ctx: BranchContext, prospect_id, body: ProspectApplicationInterestUpdateRequest
) -> MerchantProspect:
    prospect = await _get_prospect_or_404(ctx, prospect_id)

    prospect.application_interest = body.application_interest
    await ctx.db.commit()
    await ctx.db.refresh(prospect)
    return prospect


async def list_leads(ctx: BranchContext) -> list[Lead]:
    # Not ctx.scoped(Lead) — Lead's FK column is assigned_branch_id, not
    # branch_id (a lead is *assigned to* a branch, not intrinsically owned
    # by it the way a MerchantProspect is), so the generic scoped() helper
    # (which assumes a `branch_id` column) doesn't apply here.
    result = await ctx.db.scalars(
        select(Lead).where(Lead.assigned_branch_id == ctx.branch_id).order_by(Lead.created_at.desc())
    )
    return list(result)


async def respond_lead(ctx: BranchContext, lead_id, body: LeadRespondRequest) -> Lead:
    lead = await ctx.db.scalar(
        select(Lead).where(Lead.id == lead_id, Lead.assigned_branch_id == ctx.branch_id)
    )
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lead not found")

    # The SLA clock (see the case's 15-minute target) stops at the *first*
    # response regardless of which status the Champion sets it to — a lead
    # marked "lost" on first contact still counts as responded-to.
    if lead.first_response_at is None:
        lead.first_response_at = datetime.now(timezone.utc)
    lead.status = body.status
    await ctx.db.commit()
    await ctx.db.refresh(lead)
    return lead


async def leaderboard(ctx: BranchContext) -> list[LeaderboardEntryResponse]:
    """Ranks every branch, not just the caller's own — Champions are meant to
    see how they stack up against neighboring branches (see the case's
    Roadmap slide: "ร้านที่เยี่ยม / สาขา / สัปดาห์"), and this data isn't
    tenant-sensitive shop data, just each branch's own activity counts."""
    since = datetime.now(timezone.utc) - _LEADERBOARD_WINDOW

    # A prospect counts as "visited" either through the Morning Route visit
    # flow (last_visited_at) or by having been marked met (met_at) — the two
    # are different entry points to the same real-world event, an in-person
    # visit, so either one alone should count, not both at once. Both use
    # dedicated timestamps rather than current contact_status, so a prospect
    # later marked "called" again still counts as visited from having been
    # met earlier in the window — see the model's comment on called_at/met_at.
    visited_subq = (
        select(MerchantProspect.branch_id, func.count().label("visited"))
        .where(or_(MerchantProspect.last_visited_at >= since, MerchantProspect.met_at >= since))
        .group_by(MerchantProspect.branch_id)
        .subquery()
    )
    # Likewise cumulative — a prospect counts as "contacted" if it was ever
    # called within the window, even if its current status has since moved
    # on to met/unreachable.
    prospects_contacted_subq = (
        select(MerchantProspect.branch_id, func.count().label("contacted"))
        .where(MerchantProspect.called_at >= since)
        .group_by(MerchantProspect.branch_id)
        .subquery()
    )
    contacted_subq = (
        select(Lead.assigned_branch_id.label("branch_id"), func.count().label("contacted"))
        .where(Lead.first_response_at >= since)
        .group_by(Lead.assigned_branch_id)
        .subquery()
    )

    rows = (
        await ctx.db.execute(
            select(
                Branch.id,
                Branch.name,
                func.coalesce(visited_subq.c.visited, 0),
                func.coalesce(prospects_contacted_subq.c.contacted, 0),
                func.coalesce(contacted_subq.c.contacted, 0),
            )
            .outerjoin(visited_subq, visited_subq.c.branch_id == Branch.id)
            .outerjoin(prospects_contacted_subq, prospects_contacted_subq.c.branch_id == Branch.id)
            .outerjoin(contacted_subq, contacted_subq.c.branch_id == Branch.id)
        )
    ).all()

    entries = [
        LeaderboardEntryResponse(
            branch_id=row[0],
            branch_name=row[1],
            prospects_visited=row[2],
            prospects_contacted=row[3],
            leads_contacted=row[4],
            # Weighted so a Champion can't top the board on calls alone — an
            # in-person visit outweighs a call, and actually landing a lead
            # outweighs both.
            score=row[2] + row[3] + row[4] * 2,
        )
        for row in rows
    ]
    entries.sort(key=lambda e: e.score, reverse=True)
    return entries


async def list_nearby(db: AsyncSession, lat: Decimal, lng: Decimal, limit: int = 5) -> list[NearbyBranchResponse]:
    """Ranks every branch with known coordinates by great-circle distance
    from (lat, lng) — a branch never got coordinates at signup can't be
    ranked, so it's excluded rather than sorted arbitrarily. Distance math
    happens in Python (no PostGIS in this stack) since the branch count is
    small enough that this is never going to be the bottleneck."""
    branches = await db.scalars(select(Branch).where(Branch.lat.is_not(None), Branch.lng.is_not(None)))
    ranked = [
        NearbyBranchResponse(
            id=branch.id,
            code=branch.code,
            name=branch.name,
            province=branch.province,
            lat=branch.lat,
            lng=branch.lng,
            distance_km=_haversine_km(lat, lng, branch.lat, branch.lng),
        )
        for branch in branches
    ]
    ranked.sort(key=lambda b: b.distance_km)
    return ranked[:limit]
