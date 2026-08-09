import uuid
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.tenancy import TenantContext
from app.models.product import Product
from app.models.refund import Refund, RefundItem
from app.models.sale import Sale, SaleItem, SaleStatus
from app.models.stock import StockMovement, StockMovementType
from app.schemas.refund import RefundCreateRequest, RefundItemResult, RefundResult
from app.services import audit_service

_CENTS = Decimal("0.01")


async def refund_sale(ctx: TenantContext, sale_id: uuid.UUID, body: RefundCreateRequest) -> RefundResult:
    existing = await _find_by_client_uuid(ctx, body.client_uuid)
    if existing is not None:
        return await _to_refund_result(ctx, existing)

    sale = await ctx.db.scalar(ctx.scoped(Sale).where(Sale.id == sale_id))
    if sale is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sale not found")
    if sale.status not in (SaleStatus.completed, SaleStatus.partially_refunded):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "sale cannot be refunded (void, or already fully refunded)"
        )

    sale_items_by_id = {
        si.id: si for si in await ctx.db.scalars(select(SaleItem).where(SaleItem.sale_id == sale.id))
    }

    # None/empty items = refund everything still remaining on the sale.
    if body.items:
        targets = [(item.sale_item_id, item.qty) for item in body.items]
    else:
        targets = [
            (si.id, si.qty - si.refunded_qty)
            for si in sale_items_by_id.values()
            if si.qty - si.refunded_qty > 0
        ]
    if not targets:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "nothing left to refund")

    # Validate every line before mutating anything — a bad line anywhere in
    # the request must not leave the rest half-applied.
    for sale_item_id, qty in targets:
        si = sale_items_by_id.get(sale_item_id)
        if si is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "sale item not part of this sale")
        remaining = si.qty - si.refunded_qty
        if qty <= 0 or qty > remaining:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"cannot refund {qty} of {si.name_snapshot}, only {remaining} left",
            )

    product_ids = {sale_items_by_id[sid].product_id for sid, _ in targets}
    products_by_id = {
        p.id: p for p in await ctx.db.scalars(ctx.scoped(Product).where(Product.id.in_(product_ids)))
    }

    total_amount = Decimal("0")
    total_tax = Decimal("0")
    refund_items: list[RefundItem] = []
    stock_updates: list[tuple[Product, int]] = []

    # What the sale was worth before points were applied as a tender — i.e.
    # what the per-line amounts below sum to. Used to prorate points_discount
    # back out of each line; see the subtraction inside the loop.
    total_before_points = sale.total + sale.points_discount

    for sale_item_id, qty in targets:
        si = sale_items_by_id[sale_item_id]
        # Proportional share of this line's already-discounted, pre-tax value.
        unit_net = si.line_total / si.qty
        gross = unit_net * qty
        discount_share = (sale.discount * gross / sale.subtotal) if sale.subtotal else Decimal("0")
        net_before_tax = gross - discount_share

        if sale.tax_rate_snapshot:
            if sale.price_includes_tax_snapshot:
                line_tax = net_before_tax * sale.tax_rate_snapshot / (Decimal("100") + sale.tax_rate_snapshot)
                line_amount = net_before_tax
            else:
                line_tax = net_before_tax * sale.tax_rate_snapshot / Decimal("100")
                line_amount = net_before_tax + line_tax
        else:
            line_tax = Decimal("0")
            line_amount = net_before_tax

        # Points are redeemed as a tender applied *after* tax (see
        # sales_service.create_sale), so they never reached `sale.discount`
        # or the pre-tax math above — this line's cash refund has to give
        # back its proportional share of them, or we'd hand over money the
        # customer paid in points rather than cash. Tax is deliberately left
        # whole: VAT was charged and remitted on the full pre-points amount
        # regardless of how the customer settled the bill.
        if sale.points_discount and total_before_points:
            line_amount -= sale.points_discount * line_amount / total_before_points

        line_amount = line_amount.quantize(_CENTS)
        line_tax = line_tax.quantize(_CENTS)
        total_amount += line_amount
        total_tax += line_tax

        refund_items.append(
            RefundItem(tenant_id=ctx.tenant_id, sale_item_id=si.id, qty=qty, amount=line_amount)
        )
        si.refunded_qty += qty

        product = products_by_id.get(si.product_id)
        if product is not None and product.track_stock:
            stock_updates.append((product, qty))

    all_refunded = all(si.qty - si.refunded_qty == 0 for si in sale_items_by_id.values())
    if all_refunded:
        # The refund that empties a sale is forced to exactly match what's
        # left, rather than trusting the sum of per-line roundings — so a
        # customer never gets shorted or overpaid by a stray cent.
        prior_refunds = await ctx.db.scalars(select(Refund).where(Refund.sale_id == sale.id))
        prior_tax = sum((r.refund_tax for r in prior_refunds), Decimal("0"))
        total_amount = sale.total - sale.refunded_total
        total_tax = sale.tax - prior_tax

    refund = Refund(
        tenant_id=ctx.tenant_id,
        sale_id=sale.id,
        user_id=ctx.user_id,
        client_uuid=body.client_uuid,
        reason=body.reason,
        refund_amount=total_amount,
        refund_tax=total_tax,
    )
    ctx.db.add(refund)
    await ctx.db.flush()

    for ri in refund_items:
        ri.refund_id = refund.id
        ctx.db.add(ri)

    for product, qty in stock_updates:
        # Atomic increment, mirroring create_sale's atomic decrement.
        await ctx.db.execute(
            update(Product)
            .where(Product.id == product.id, Product.tenant_id == ctx.tenant_id)
            .values(stock_qty=Product.stock_qty + qty)
        )
        ctx.db.add(
            StockMovement(
                tenant_id=ctx.tenant_id,
                product_id=product.id,
                type=StockMovementType.return_,
                qty_delta=qty,
                unit_cost=product.cost_price,
                ref_id=refund.id,
                user_id=ctx.user_id,
                note=f"คืนสินค้า {sale.receipt_no}",
            )
        )

    sale.refunded_total += total_amount
    sale.status = SaleStatus.refunded if all_refunded else SaleStatus.partially_refunded

    await audit_service.record(ctx, "sale.refund", f"คืนเงิน {sale.receipt_no} จำนวน {total_amount} บาท")

    try:
        await ctx.db.commit()
    except IntegrityError:
        await ctx.db.rollback()
        # Lost the race to a concurrent submission of the same client_uuid.
        existing = await _find_by_client_uuid(ctx, body.client_uuid)
        if existing is not None:
            return await _to_refund_result(ctx, existing)
        raise

    await ctx.db.refresh(refund)
    return await _to_refund_result(ctx, refund)


async def _find_by_client_uuid(ctx: TenantContext, client_uuid) -> Refund | None:
    return await ctx.db.scalar(ctx.scoped(Refund).where(Refund.client_uuid == client_uuid))


async def _to_refund_result(ctx: TenantContext, refund: Refund) -> RefundResult:
    sale = await ctx.db.get(Sale, refund.sale_id)
    items = await ctx.db.scalars(select(RefundItem).where(RefundItem.refund_id == refund.id))
    return RefundResult(
        id=refund.id,
        sale_id=refund.sale_id,
        refund_amount=refund.refund_amount,
        refund_tax=refund.refund_tax,
        reason=refund.reason,
        created_at=refund.created_at,
        items=[RefundItemResult(sale_item_id=i.sale_item_id, qty=i.qty, amount=i.amount) for i in items],
        sale_status=sale.status,
    )
