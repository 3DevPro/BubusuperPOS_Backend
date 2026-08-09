import uuid

from fastapi import APIRouter, Depends, Query

from app.core.deps import require
from app.core.permissions import Permission
from app.core.tenancy import TenantContext
from app.models.turbo.insurance import InsuranceClaim, InsurancePolicy, InsuranceProduct
from app.schemas.turbo.insurance import (
    DetectedClaimResponse,
    InsuranceClaimCreateRequest,
    InsuranceClaimResponse,
    InsurancePolicyResponse,
    InsuranceProductResponse,
    InsurancePurchaseRequest,
    InsuranceQuoteResponse,
)
from app.services.turbo import claim_service, insurance_service

router = APIRouter(prefix="/insurance", tags=["turbo"])


@router.get("/products", response_model=list[InsuranceProductResponse])
async def list_products(
    ctx: TenantContext = Depends(require(Permission.manage_insurance)),
) -> list[InsuranceProduct]:
    return await insurance_service.list_products(ctx)


@router.get("/quote", response_model=InsuranceQuoteResponse)
async def quote(
    product_code: str = Query(...),
    ctx: TenantContext = Depends(require(Permission.manage_insurance)),
) -> InsuranceQuoteResponse:
    _, quoted, _ = await insurance_service.quote(ctx, product_code)
    return quoted


@router.post("/policies", response_model=InsurancePolicyResponse, status_code=201)
async def purchase_policy(
    body: InsurancePurchaseRequest,
    ctx: TenantContext = Depends(require(Permission.manage_insurance)),
) -> InsurancePolicy:
    return await insurance_service.purchase(ctx, body.product_code)


@router.get("/policies", response_model=list[InsurancePolicyResponse])
async def list_policies(
    ctx: TenantContext = Depends(require(Permission.manage_insurance)),
) -> list[InsurancePolicy]:
    return await insurance_service.list_policies(ctx)


@router.get("/claims/detected", response_model=list[DetectedClaimResponse])
async def detected_claims(
    policy_id: uuid.UUID = Query(...),
    ctx: TenantContext = Depends(require(Permission.manage_insurance)),
) -> list[DetectedClaimResponse]:
    return await claim_service.detect_claimable_periods(ctx, policy_id)


@router.post("/claims", response_model=InsuranceClaimResponse, status_code=201)
async def create_claim(
    body: InsuranceClaimCreateRequest,
    ctx: TenantContext = Depends(require(Permission.manage_insurance)),
) -> InsuranceClaim:
    return await claim_service.create_claim(ctx, body.policy_id, body.start_date, body.end_date)


@router.get("/claims", response_model=list[InsuranceClaimResponse])
async def list_claims(
    ctx: TenantContext = Depends(require(Permission.manage_insurance)),
) -> list[InsuranceClaim]:
    return await claim_service.list_claims(ctx)
