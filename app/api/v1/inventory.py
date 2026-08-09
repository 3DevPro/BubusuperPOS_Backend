from fastapi import APIRouter, Depends

from app.core.deps import require
from app.core.permissions import Permission
from app.core.tenancy import TenantContext
from app.schemas.inventory import LowStockItem, StockAdjustRequest, StockAdjustResult
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
