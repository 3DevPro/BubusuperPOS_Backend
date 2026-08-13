from fastapi import APIRouter, Depends, Query

from app.core.deps import require
from app.core.permissions import Permission
from app.core.tenancy import TenantContext
from app.schemas.inventory import ExpiringSoonItem, LowStockItem, StockAdjustRequest, StockAdjustResult
from app.services import inventory_service

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.post("/adjust", response_model=StockAdjustResult)
async def adjust_stock(
    body: StockAdjustRequest,
    ctx: TenantContext = Depends(require(Permission.adjust_inventory)),
) -> StockAdjustResult:
    return await inventory_service.adjust_stock(ctx, body)


@router.get("/low-stock", response_model=list[LowStockItem])
async def low_stock(
    ctx: TenantContext = Depends(require(Permission.view_products)),
) -> list:
    return await inventory_service.list_low_stock(ctx)


@router.get("/expiring-soon", response_model=list[ExpiringSoonItem])
async def expiring_soon(
    days: int = Query(default=7, ge=1, le=90),
    ctx: TenantContext = Depends(require(Permission.view_products)),
) -> list:
    return await inventory_service.list_expiring_soon(ctx, days)
