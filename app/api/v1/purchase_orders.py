import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.deps import require
from app.core.permissions import Permission
from app.core.tenancy import TenantContext
from app.models.purchase_order import PurchaseOrder, PurchaseOrderStatus
from app.schemas.purchase_order import (
    PurchaseOrderCreateRequest,
    PurchaseOrderListItem,
    PurchaseOrderReceiveRequest,
    PurchaseOrderResult,
)
from app.services import purchase_order_service

router = APIRouter(prefix="/purchase-orders", tags=["purchase-orders"])


@router.get("", response_model=list[PurchaseOrderListItem])
async def list_purchase_orders(
    status: PurchaseOrderStatus | None = None,
    ctx: TenantContext = Depends(require(Permission.adjust_inventory)),
) -> list[PurchaseOrder]:
    query = ctx.scoped(PurchaseOrder)
    if status is not None:
        query = query.where(PurchaseOrder.status == status)
    result = await ctx.db.scalars(query.order_by(PurchaseOrder.created_at.desc()))
    return list(result)


@router.get("/{po_id}", response_model=PurchaseOrderResult)
async def get_purchase_order(
    po_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.adjust_inventory)),
) -> PurchaseOrderResult:
    po = await ctx.db.scalar(ctx.scoped(PurchaseOrder).where(PurchaseOrder.id == po_id))
    if po is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "purchase order not found")
    return await purchase_order_service.to_purchase_order_result(ctx, po)


@router.post("", response_model=PurchaseOrderResult, status_code=201)
async def create_purchase_order(
    body: PurchaseOrderCreateRequest,
    ctx: TenantContext = Depends(require(Permission.adjust_inventory)),
) -> PurchaseOrderResult:
    return await purchase_order_service.create_purchase_order(ctx, body)


@router.post("/{po_id}/receive", response_model=PurchaseOrderResult)
async def receive_purchase_order(
    po_id: uuid.UUID,
    body: PurchaseOrderReceiveRequest,
    ctx: TenantContext = Depends(require(Permission.adjust_inventory)),
) -> PurchaseOrderResult:
    return await purchase_order_service.receive_purchase_order(ctx, po_id, body)


@router.post("/{po_id}/cancel", response_model=PurchaseOrderResult)
async def cancel_purchase_order(
    po_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.adjust_inventory)),
) -> PurchaseOrderResult:
    return await purchase_order_service.cancel_purchase_order(ctx, po_id)
