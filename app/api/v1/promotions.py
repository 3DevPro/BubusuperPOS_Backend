import uuid

from fastapi import APIRouter, Depends, status

from app.core.deps import require
from app.core.permissions import Permission
from app.core.tenancy import TenantContext
from app.schemas.promotion import PromotionCreateRequest, PromotionResponse, PromotionUpdateRequest
from app.services import promotion_service

router = APIRouter(prefix="/promotions", tags=["promotions"])


@router.get("", response_model=list[PromotionResponse])
async def list_promotions(
    ctx: TenantContext = Depends(require(Permission.view_products)),
) -> list[PromotionResponse]:
    return await promotion_service.list_promotions(ctx)


@router.get("/active", response_model=list[PromotionResponse])
async def list_active_promotions(
    ctx: TenantContext = Depends(require(Permission.create_sale)),
) -> list[PromotionResponse]:
    return await promotion_service.list_promotions(ctx, active_only=True)


@router.post("", response_model=PromotionResponse, status_code=status.HTTP_201_CREATED)
async def create_promotion(
    body: PromotionCreateRequest,
    ctx: TenantContext = Depends(require(Permission.manage_promotions)),
) -> PromotionResponse:
    return await promotion_service.create_promotion(ctx, body)


@router.patch("/{promotion_id}", response_model=PromotionResponse)
async def update_promotion(
    promotion_id: uuid.UUID,
    body: PromotionUpdateRequest,
    ctx: TenantContext = Depends(require(Permission.manage_promotions)),
) -> PromotionResponse:
    return await promotion_service.update_promotion(ctx, promotion_id, body)


@router.delete("/{promotion_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_promotion(
    promotion_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.manage_promotions)),
) -> None:
    await promotion_service.deactivate_promotion(ctx, promotion_id)
