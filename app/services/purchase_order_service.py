import uuid

from fastapi import HTTPException, status
from sqlalchemy import select, update

from app.core.tenancy import TenantContext
from app.models.product import Product
from app.models.purchase_order import PurchaseOrder, PurchaseOrderItem, PurchaseOrderStatus
from app.models.stock import StockMovement, StockMovementType
from app.models.supplier import Supplier
from app.models.tenant import Tenant
from app.schemas.purchase_order import (
    PurchaseOrderCreateRequest,
    PurchaseOrderItemResult,
    PurchaseOrderReceiveRequest,
    PurchaseOrderResult,
)
from app.services import audit_service


async def create_purchase_order(ctx: TenantContext, body: PurchaseOrderCreateRequest) -> PurchaseOrderResult:
    if not body.items:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "purchase order must have at least one item")

    supplier = await ctx.db.scalar(ctx.scoped(Supplier).where(Supplier.id == body.supplier_id))
    if supplier is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "supplier not found")

    product_ids = [item.product_id for item in body.items]
    products = await ctx.db.scalars(ctx.scoped(Product).where(Product.id.in_(product_ids)))
    products_by_id = {p.id: p for p in products}
    missing = [str(pid) for pid in product_ids if pid not in products_by_id]
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown product(s): {', '.join(missing)}")

    order_no = await _next_order_no(ctx)
    po = PurchaseOrder(
        tenant_id=ctx.tenant_id,
        order_no=order_no,
        supplier_id=supplier.id,
        user_id=ctx.user_id,
        status=PurchaseOrderStatus.ordered,
        notes=body.notes,
    )
    ctx.db.add(po)
    await ctx.db.flush()

    for item in body.items:
        product = products_by_id[item.product_id]
        ctx.db.add(
            PurchaseOrderItem(
                tenant_id=ctx.tenant_id,
                purchase_order_id=po.id,
                product_id=product.id,
                name_snapshot=product.name,
                qty_ordered=item.qty,
                unit_cost=item.unit_cost,
            )
        )

    await audit_service.record(ctx, "purchase_order.create", f"สั่งซื้อ {order_no} จาก {supplier.name}")
    await ctx.db.commit()
    await ctx.db.refresh(po)
    return await to_purchase_order_result(ctx, po)


async def receive_purchase_order(
    ctx: TenantContext, po_id: uuid.UUID, body: PurchaseOrderReceiveRequest
) -> PurchaseOrderResult:
    po = await ctx.db.scalar(ctx.scoped(PurchaseOrder).where(PurchaseOrder.id == po_id))
    if po is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "purchase order not found")
    if po.status not in (PurchaseOrderStatus.ordered, PurchaseOrderStatus.partially_received):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "purchase order cannot be received (cancelled or already fully received)"
        )
    if not body.items:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "must receive at least one item")

    items_by_id = {
        i.id: i
        for i in await ctx.db.scalars(
            select(PurchaseOrderItem).where(PurchaseOrderItem.purchase_order_id == po.id)
        )
    }

    # Validate every line before mutating anything — a bad line anywhere in
    # the request must not leave the rest half-applied, same rule as
    # refund_service.
    for entry in body.items:
        poi = items_by_id.get(entry.purchase_order_item_id)
        if poi is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "item not part of this purchase order")
        remaining = poi.qty_ordered - poi.qty_received
        if entry.qty <= 0 or entry.qty > remaining:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"cannot receive {entry.qty} of {poi.name_snapshot}, only {remaining} left",
            )

    product_ids = {items_by_id[e.purchase_order_item_id].product_id for e in body.items}
    products_by_id = {
        p.id: p for p in await ctx.db.scalars(ctx.scoped(Product).where(Product.id.in_(product_ids)))
    }

    for entry in body.items:
        poi = items_by_id[entry.purchase_order_item_id]
        poi.qty_received += entry.qty

        product = products_by_id.get(poi.product_id)
        if product is not None and product.track_stock:
            # Atomic increment, same pattern as refund_service's restock.
            await ctx.db.execute(
                update(Product)
                .where(Product.id == product.id, Product.tenant_id == ctx.tenant_id)
                .values(stock_qty=Product.stock_qty + entry.qty)
            )
            ctx.db.add(
                StockMovement(
                    tenant_id=ctx.tenant_id,
                    product_id=product.id,
                    type=StockMovementType.purchase,
                    qty_delta=entry.qty,
                    unit_cost=poi.unit_cost,
                    ref_id=po.id,
                    user_id=ctx.user_id,
                    note=f"รับของ {po.order_no}",
                )
            )

    all_received = all(i.qty_received >= i.qty_ordered for i in items_by_id.values())
    po.status = PurchaseOrderStatus.received if all_received else PurchaseOrderStatus.partially_received

    await audit_service.record(ctx, "purchase_order.receive", f"รับของเข้าคลัง {po.order_no}")
    await ctx.db.commit()
    await ctx.db.refresh(po)
    return await to_purchase_order_result(ctx, po)


async def cancel_purchase_order(ctx: TenantContext, po_id: uuid.UUID) -> PurchaseOrderResult:
    po = await ctx.db.scalar(ctx.scoped(PurchaseOrder).where(PurchaseOrder.id == po_id))
    if po is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "purchase order not found")
    if po.status != PurchaseOrderStatus.ordered:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "only an order with nothing received yet can be cancelled"
        )
    po.status = PurchaseOrderStatus.cancelled
    await audit_service.record(ctx, "purchase_order.cancel", f"ยกเลิกคำสั่งซื้อ {po.order_no}")
    await ctx.db.commit()
    await ctx.db.refresh(po)
    return await to_purchase_order_result(ctx, po)


async def _next_order_no(ctx: TenantContext) -> str:
    result = await ctx.db.execute(
        update(Tenant)
        .where(Tenant.id == ctx.tenant_id)
        .values(po_counter=Tenant.po_counter + 1)
        .returning(Tenant.po_counter)
    )
    counter = result.scalar_one()
    return f"PO{counter:06d}"


async def to_purchase_order_result(ctx: TenantContext, po: PurchaseOrder) -> PurchaseOrderResult:
    items = await ctx.db.scalars(
        select(PurchaseOrderItem).where(PurchaseOrderItem.purchase_order_id == po.id)
    )
    return PurchaseOrderResult(
        id=po.id,
        order_no=po.order_no,
        supplier_id=po.supplier_id,
        status=po.status,
        notes=po.notes,
        created_at=po.created_at,
        items=[
            PurchaseOrderItemResult(
                id=i.id,
                product_id=i.product_id,
                name=i.name_snapshot,
                qty_ordered=i.qty_ordered,
                qty_received=i.qty_received,
                unit_cost=i.unit_cost,
            )
            for i in items
        ],
    )
