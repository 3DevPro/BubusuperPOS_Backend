import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import CurrentBranch, CurrentTenant
# Reuses auth.py's own token-issuing functions rather than importing its
# private _issue_tokens — see the branch_signup handler below.
from app.core.security import create_access_token, create_refresh_token
from app.models.turbo.branch import Lead, MerchantProspect
from app.schemas.auth import TokenResponse
from app.schemas.turbo.branch import (
    BranchSignupRequest,
    LeaderboardEntryResponse,
    LeadRespondRequest,
    LeadResponse,
    NearbyBranchResponse,
    ProspectCreateRequest,
    ProspectResponse,
    ProspectVisitRequest,
)
from app.services.turbo import branch_service

router = APIRouter(prefix="/branch", tags=["turbo-branch"])


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def branch_signup(body: BranchSignupRequest, db: Annotated[AsyncSession, Depends(get_db)]) -> TokenResponse:
    staff = await branch_service.signup(db, body)
    return TokenResponse(
        access_token=create_access_token(staff.id, None, staff.role.value, branch_id=staff.branch_id),
        refresh_token=create_refresh_token(staff.id, None, staff.role.value, branch_id=staff.branch_id),
    )


@router.get("/nearby", response_model=list[NearbyBranchResponse])
async def nearby_branches(lat: Decimal, lng: Decimal, ctx: CurrentTenant) -> list[NearbyBranchResponse]:
    return await branch_service.list_nearby(ctx.db, lat, lng)


@router.get("/prospects", response_model=list[ProspectResponse])
async def list_prospects(ctx: CurrentBranch) -> list[MerchantProspect]:
    return await branch_service.list_prospects(ctx)


@router.post("/prospects", response_model=ProspectResponse, status_code=status.HTTP_201_CREATED)
async def create_prospect(body: ProspectCreateRequest, ctx: CurrentBranch) -> MerchantProspect:
    return await branch_service.create_prospect(ctx, body)


@router.post("/prospects/{prospect_id}/visit", response_model=ProspectResponse)
async def visit_prospect(prospect_id: uuid.UUID, body: ProspectVisitRequest, ctx: CurrentBranch) -> MerchantProspect:
    return await branch_service.visit_prospect(ctx, prospect_id, body)


@router.get("/leads", response_model=list[LeadResponse])
async def list_leads(ctx: CurrentBranch) -> list[Lead]:
    return await branch_service.list_leads(ctx)


@router.post("/leads/{lead_id}/respond", response_model=LeadResponse)
async def respond_lead(lead_id: uuid.UUID, body: LeadRespondRequest, ctx: CurrentBranch) -> Lead:
    return await branch_service.respond_lead(ctx, lead_id, body)


@router.get("/leaderboard", response_model=list[LeaderboardEntryResponse])
async def leaderboard(ctx: CurrentBranch) -> list[LeaderboardEntryResponse]:
    return await branch_service.leaderboard(ctx)
