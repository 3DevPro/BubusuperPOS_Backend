from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.tenancy import TenantContext
from app.models.customer import Customer
from app.models.product import Product
from app.models.refund import Refund
from app.models.sale import Sale, SaleItem, SaleStatus
from app.models.stock import StockMovement, StockMovementType
from app.models.tenant import Tenant
from app.schemas.sale import RefundSummary, SaleCreateRequest, SaleItemResult, SaleResult
from app.services import pricing


async def create_sale(ctx: TenantContext, body: SaleCreateRequest) -> SaleResult:
    existing = await _find_by_client_uuid(ctx, body.client_uuid)
    if existing is not None:
        return await to_sale_result(ctx, existing)

    if not body.items:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "sale must have at least one item")

    tenant = await ctx.db.get(Tenant, ctx.tenant_id)

    customer: Customer | None = None
    if body.customer_id is not None:
        customer = await ctx.db.scalar(ctx.scoped(Customer).where(Customer.id == body.customer_id))
        if customer is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "customer not found")

    product_ids = [item.product_id for item in body.items]
    products = await ctx.db.scalars(ctx.scoped(Product).where(Product.id.in_(product_ids)))
    products_by_id = {p.id: p for p in products}

    missing = [str(pid) for pid in product_ids if pid not in products_by_id]
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown product(s): {', '.join(missing)}")

    # Prices/costs always come from the catalog server-side, never from the
    # client — otherwise a tampered request could check out at any price. The
    # same rule applies to per-item discounts: validate against the
    # server-known price, never trust a client-computed line_total.
    line_totals: list[Decimal] = []
    sale_items: list[SaleItem] = []
    warnings: list[str] = []

    for item in body.items:
        product = products_by_id[item.product_id]
        gross = product.sell_price * item.qty
        if item.discount > gross:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"discount exceeds line total for {product.name}"
            )
        line_total = gross - item.discount
        line_totals.append(line_total)
        sale_items.append(
            SaleItem(
                tenant_id=ctx.tenant_id,
                product_id=product.id,
                name_snapshot=product.name,
                price_snapshot=product.sell_price,
                cost_snapshot=product.cost_price,
                qty=item.qty,
                discount=item.discount,
                line_total=line_total,
            )
        )
        if product.track_stock and product.stock_qty - item.qty < 0:
            warnings.append(f"{product.name} สต็อกไม่พอ (มี {product.stock_qty} ขาย {item.qty})")

    try:
        totals = pricing.calc_totals(
            line_totals,
            body.discount,
            tenant.vat_enabled,
            tenant.vat_rate,
            tenant.price_includes_tax,
        )
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "discount exceeds subtotal")

    # Points redemption is deliberately kept out of pricing.calc_totals() and
    # Sale.discount — it's a separate, non-proportional reduction applied
    # after tax, so refund_service's discount/subtotal-based proration never
    # claws back points value (refunds don't adjust points, by design).
    points_redeemed = 0
    points_discount = Decimal("0")
    if body.redeem_points:
        if customer is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "redeem_points requires a customer")
        if not tenant.loyalty_enabled:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "loyalty points are not enabled for this shop")
        if body.redeem_points > customer.points_balance:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "not enough points")
        points_redeemed = body.redeem_points
        points_discount = min(totals.total, tenant.point_value_baht * points_redeemed)

    final_total = totals.total - points_discount

    points_earned = 0
    if tenant.loyalty_enabled and customer is not None and tenant.baht_per_point > 0:
        # Earned on the amount actually paid, not the pre-redemption total —
        # otherwise a customer would earn points on money they didn't spend.
        points_earned = int(final_total // tenant.baht_per_point)

    receipt_no = await _next_receipt_no(ctx)

    sale = Sale(
        tenant_id=ctx.tenant_id,
        receipt_no=receipt_no,
        user_id=ctx.user_id,
        client_uuid=body.client_uuid,
        customer_id=customer.id if customer is not None else None,
        subtotal=totals.subtotal,
        discount=totals.discount,
        tax=totals.tax,
        tax_rate_snapshot=tenant.vat_rate if tenant.vat_enabled else Decimal("0"),
        price_includes_tax_snapshot=tenant.price_includes_tax,
        total=final_total,
        points_earned=points_earned,
        points_redeemed=points_redeemed,
        points_discount=points_discount,
        payment_method=body.payment_method,
        status=SaleStatus.completed,
    )
    ctx.db.add(sale)
    await ctx.db.flush()

    for sale_item in sale_items:
        sale_item.sale_id = sale.id
        ctx.db.add(sale_item)

    for item in body.items:
        product = products_by_id[item.product_id]
        if not product.track_stock:
            continue
        # Atomic decrement (stock_qty = stock_qty - qty) so two concurrent
        # checkouts can't read-modify-write over each other. Going negative is
        # allowed on purpose — a queued customer shouldn't be blocked; the
        # warning above is what tells the cashier stock ran out.
        await ctx.db.execute(
            update(Product)
            .where(Product.id == product.id, Product.tenant_id == ctx.tenant_id)
            .values(stock_qty=Product.stock_qty - item.qty)
        )
        ctx.db.add(
            StockMovement(
                tenant_id=ctx.tenant_id,
                product_id=product.id,
                type=StockMovementType.sale,
                qty_delta=-item.qty,
                unit_cost=product.cost_price,
                ref_id=sale.id,
                user_id=ctx.user_id,
                note=f"ขาย {receipt_no}",
            )
        )

    if customer is not None:
        # Atomic, same reasoning as the stock decrement above — two
        # concurrent checkouts for the same customer must not read-modify-
        # write over each other's points balance.
        await ctx.db.execute(
            update(Customer)
            .where(Customer.id == customer.id, Customer.tenant_id == ctx.tenant_id)
            .values(points_balance=Customer.points_balance - points_redeemed + points_earned)
        )

    try:
        await ctx.db.commit()
    except IntegrityError:
        await ctx.db.rollback()
        # Lost the race to a concurrent submission of the same client_uuid.
        existing = await _find_by_client_uuid(ctx, body.client_uuid)
        if existing is not None:
            return await to_sale_result(ctx, existing)
        raise

    await ctx.db.refresh(sale)
    return await to_sale_result(ctx, sale, warnings=warnings)


async def _find_by_client_uuid(ctx: TenantContext, client_uuid) -> Sale | None:
    return await ctx.db.scalar(ctx.scoped(Sale).where(Sale.client_uuid == client_uuid))


async def _next_receipt_no(ctx: TenantContext) -> str:
    result = await ctx.db.execute(
        update(Tenant)
        .where(Tenant.id == ctx.tenant_id)
        .values(receipt_counter=Tenant.receipt_counter + 1)
        .returning(Tenant.receipt_counter)
    )
    counter = result.scalar_one()
    return f"R{counter:06d}"


async def to_sale_result(ctx: TenantContext, sale: Sale, warnings: list[str] | None = None) -> SaleResult:
    items = await ctx.db.scalars(select(SaleItem).where(SaleItem.sale_id == sale.id))
    refunds = await ctx.db.scalars(
        select(Refund).where(Refund.sale_id == sale.id).order_by(Refund.created_at)
    )
    return SaleResult(
        id=sale.id,
        receipt_no=sale.receipt_no,
        customer_id=sale.customer_id,
        subtotal=sale.subtotal,
        discount=sale.discount,
        tax=sale.tax,
        price_includes_tax=sale.price_includes_tax_snapshot,
        total=sale.total,
        points_earned=sale.points_earned,
        points_redeemed=sale.points_redeemed,
        points_discount=sale.points_discount,
        payment_method=sale.payment_method,
        status=sale.status,
        refunded_total=sale.refunded_total,
        created_at=sale.created_at,
        items=[
            SaleItemResult(
                id=i.id,
                product_id=i.product_id,
                name=i.name_snapshot,
                price=i.price_snapshot,
                qty=i.qty,
                discount=i.discount,
                line_total=i.line_total,
                refunded_qty=i.refunded_qty,
            )
            for i in items
        ],
        refunds=[
            RefundSummary(
                id=r.id,
                refund_amount=r.refund_amount,
                refund_tax=r.refund_tax,
                reason=r.reason,
                created_at=r.created_at,
            )
            for r in refunds
        ],
        warnings=warnings or [],
    )
