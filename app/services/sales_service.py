import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
from app.schemas.promotion import AppliedPromotionResult
from app.schemas.sale import (
    PreviewLineResult,
    RefundSummary,
    SaleCreateRequest,
    SaleItemRequest,
    SaleItemResult,
    SalePreviewRequest,
    SalePreviewResult,
    SaleResult,
)
from app.services import pricing, promotion_service
from app.services.promotion_engine import CartLineProduct, RequestedLine, ResolvedCart, resolve


@dataclass
class _ResolvedCheckout:
    resolved: ResolvedCart
    totals: pricing.TotalsResult
    tenant: Tenant
    customer: Customer | None
    products_by_id: dict[uuid.UUID, Product]
    points_redeemed: int
    points_discount: Decimal
    final_total: Decimal
    points_earned: int
    warnings: list[str]


def _clamp_occurred_at(occurred_at: datetime | None) -> datetime:
    """None (the normal, online-checkout case) means "evaluate promotions
    as of right now". A value from an offline-queued sale being replayed is
    honored as long as it's not from the future and not implausibly old
    (more than 7 days, past any reasonable offline-queue lifetime) — either
    of those falls back to "now" rather than trusting a client-supplied
    timestamp that could otherwise be used to game a promotion's window."""
    now = datetime.now(timezone.utc)
    if occurred_at is None:
        return now
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    if occurred_at > now or (now - occurred_at) > timedelta(days=7):
        return now
    return occurred_at


async def _resolve_checkout(
    ctx: TenantContext,
    items: list[SaleItemRequest],
    discount: Decimal,
    customer_id: uuid.UUID | None,
    redeem_points: int,
    coupon_code: str | None,
    occurred_at: datetime | None,
) -> _ResolvedCheckout:
    """The shared resolve-then-total path behind both create_sale and
    preview_sale — the single place cart lines turn into priced,
    promotion-applied totals, so a cart preview can never drift from what
    checkout actually charges."""
    tenant = await ctx.db.get(Tenant, ctx.tenant_id)

    customer: Customer | None = None
    if customer_id is not None:
        customer = await ctx.db.scalar(ctx.scoped(Customer).where(Customer.id == customer_id))
        if customer is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "customer not found")

    product_ids = [item.product_id for item in items]
    products = await ctx.db.scalars(ctx.scoped(Product).where(Product.id.in_(product_ids)))
    products_by_id = {p.id: p for p in products}
    missing = [str(pid) for pid in product_ids if pid not in products_by_id]
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown product(s): {', '.join(missing)}")

    evaluated_at = _clamp_occurred_at(occurred_at)

    requested = [
        RequestedLine(
            product=CartLineProduct(
                id=product.id,
                name=product.name,
                sell_price=product.sell_price,
                cost_price=product.cost_price,
                category_id=product.category_id,
            ),
            qty=item.qty,
            manual_discount=item.discount,
        )
        for item in items
        for product in [products_by_id[item.product_id]]
    ]

    promotions = await promotion_service.load_for_engine(ctx)
    resolved = resolve(requested, promotions, coupon_code, evaluated_at)

    if coupon_code and not resolved.coupon_matched:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "รหัสคูปองไม่ถูกต้องหรือหมดอายุ")

    for line in resolved.lines:
        if line.manual_discount + line.promo_discount > line.gross:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"ส่วนลดเกินราคาสินค้า {line.name_snapshot}")

    line_totals = [line.line_total for line in resolved.lines]
    try:
        totals = pricing.calc_totals(
            line_totals, discount, tenant.vat_enabled, tenant.vat_rate, tenant.price_includes_tax
        )
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "discount exceeds subtotal")

    # Points redemption is deliberately kept out of pricing.calc_totals() and
    # Sale.discount — it's a separate, non-proportional reduction applied
    # after tax, so refund_service's discount/subtotal-based proration never
    # claws back points value (refunds don't adjust points, by design).
    points_redeemed = 0
    points_discount = Decimal("0")
    if redeem_points:
        if customer is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "redeem_points requires a customer")
        if not tenant.loyalty_enabled:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "loyalty points are not enabled for this shop")
        if redeem_points > customer.points_balance:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "not enough points")
        points_redeemed = redeem_points
        points_discount = min(totals.total, tenant.point_value_baht * points_redeemed)

    final_total = totals.total - points_discount

    points_earned = 0
    if tenant.loyalty_enabled and customer is not None and tenant.baht_per_point > 0:
        points_earned = int(final_total // tenant.baht_per_point)

    # Grouped by product so a product split across a paid + free-gift line
    # (buy_n_get_m) surfaces one warning, not two.
    qty_by_product: dict[uuid.UUID, int] = {}
    for line in resolved.lines:
        qty_by_product[line.product_id] = qty_by_product.get(line.product_id, 0) + line.qty
    warnings = [
        f"{products_by_id[pid].name} สต็อกไม่พอ (มี {products_by_id[pid].stock_qty} ขาย {qty})"
        for pid, qty in qty_by_product.items()
        if products_by_id[pid].track_stock and products_by_id[pid].stock_qty - qty < 0
    ]

    return _ResolvedCheckout(
        resolved=resolved,
        totals=totals,
        tenant=tenant,
        customer=customer,
        products_by_id=products_by_id,
        points_redeemed=points_redeemed,
        points_discount=points_discount,
        final_total=final_total,
        points_earned=points_earned,
        warnings=warnings,
    )


async def preview_sale(ctx: TenantContext, body: SalePreviewRequest) -> SalePreviewResult:
    if not body.items:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "sale must have at least one item")

    checkout = await _resolve_checkout(
        ctx, body.items, body.discount, body.customer_id, body.redeem_points, body.coupon_code, occurred_at=None
    )
    return SalePreviewResult(
        subtotal=checkout.totals.subtotal,
        discount=checkout.totals.discount,
        tax=checkout.totals.tax,
        total=checkout.final_total,
        price_includes_tax=checkout.tenant.price_includes_tax,
        points_earned=checkout.points_earned,
        points_redeemed=checkout.points_redeemed,
        points_discount=checkout.points_discount,
        items=[
            PreviewLineResult(
                product_id=line.product_id,
                name=line.name_snapshot,
                price=line.price_snapshot,
                qty=line.qty,
                discount=line.manual_discount,
                promo_discount=line.promo_discount,
                promotion_name=line.promotion_name_snapshot,
                is_free_gift=line.is_free_gift,
                line_total=line.line_total,
            )
            for line in checkout.resolved.lines
        ],
        applied_promotions=[
            AppliedPromotionResult(promotion_id=a.promotion_id, name=a.name, discount_amount=a.discount_amount)
            for a in checkout.resolved.applied
        ],
        warnings=checkout.warnings,
    )


async def create_sale(ctx: TenantContext, body: SaleCreateRequest) -> SaleResult:
    existing = await _find_by_client_uuid(ctx, body.client_uuid)
    if existing is not None:
        return await to_sale_result(ctx, existing)

    if not body.items:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "sale must have at least one item")

    checkout = await _resolve_checkout(
        ctx, body.items, body.discount, body.customer_id, body.redeem_points, body.coupon_code, body.occurred_at
    )

    receipt_no = await _next_receipt_no(ctx)

    sale = Sale(
        tenant_id=ctx.tenant_id,
        receipt_no=receipt_no,
        user_id=ctx.user_id,
        client_uuid=body.client_uuid,
        customer_id=checkout.customer.id if checkout.customer is not None else None,
        subtotal=checkout.totals.subtotal,
        discount=checkout.totals.discount,
        tax=checkout.totals.tax,
        tax_rate_snapshot=checkout.tenant.vat_rate if checkout.tenant.vat_enabled else Decimal("0"),
        price_includes_tax_snapshot=checkout.tenant.price_includes_tax,
        total=checkout.final_total,
        points_earned=checkout.points_earned,
        points_redeemed=checkout.points_redeemed,
        points_discount=checkout.points_discount,
        payment_method=body.payment_method,
        status=SaleStatus.completed,
    )
    ctx.db.add(sale)
    await ctx.db.flush()

    for line in checkout.resolved.lines:
        ctx.db.add(
            SaleItem(
                tenant_id=ctx.tenant_id,
                sale_id=sale.id,
                product_id=line.product_id,
                name_snapshot=line.name_snapshot,
                price_snapshot=line.price_snapshot,
                cost_snapshot=line.cost_snapshot,
                qty=line.qty,
                discount=line.manual_discount,
                promo_discount=line.promo_discount,
                promotion_id=line.promotion_id,
                promotion_name_snapshot=line.promotion_name_snapshot,
                is_free_gift=line.is_free_gift,
                line_total=line.line_total,
            )
        )

    # Iterates resolved lines, not body.items — a buy_n_get_m line splits
    # one requested item into a paid line and a free-gift line, and both
    # must decrement stock (the free units are real units leaving the
    # shelf) and leave their own StockMovement row.
    for line in checkout.resolved.lines:
        product = checkout.products_by_id[line.product_id]
        if not product.track_stock:
            continue
        # Atomic decrement (stock_qty = stock_qty - qty) so two concurrent
        # checkouts can't read-modify-write over each other. Going negative is
        # allowed on purpose — a queued customer shouldn't be blocked; the
        # warning above is what tells the cashier stock ran out.
        await ctx.db.execute(
            update(Product)
            .where(Product.id == product.id, Product.tenant_id == ctx.tenant_id)
            .values(stock_qty=Product.stock_qty - line.qty)
        )
        ctx.db.add(
            StockMovement(
                tenant_id=ctx.tenant_id,
                product_id=product.id,
                type=StockMovementType.sale,
                qty_delta=-line.qty,
                unit_cost=product.cost_price,
                ref_id=sale.id,
                user_id=ctx.user_id,
                note=f"ขาย {receipt_no} (แถม)" if line.is_free_gift else f"ขาย {receipt_no}",
            )
        )

    if checkout.customer is not None:
        # Atomic, same reasoning as the stock decrement above — two
        # concurrent checkouts for the same customer must not read-modify-
        # write over each other's points balance.
        await ctx.db.execute(
            update(Customer)
            .where(Customer.id == checkout.customer.id, Customer.tenant_id == ctx.tenant_id)
            .values(points_balance=Customer.points_balance - checkout.points_redeemed + checkout.points_earned)
        )

    await promotion_service.record_uses(ctx, [a.promotion_id for a in checkout.resolved.applied])

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
    return await to_sale_result(ctx, sale, warnings=checkout.warnings)


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
    items = list(await ctx.db.scalars(select(SaleItem).where(SaleItem.sale_id == sale.id)))
    refunds = await ctx.db.scalars(
        select(Refund).where(Refund.sale_id == sale.id).order_by(Refund.created_at)
    )

    # Derived from the line items rather than a separate sale-level table —
    # promo_discount + promotion_id/name_snapshot on SaleItem already carry
    # everything needed to attribute "what did this promotion cost me" per
    # sale, without a second write path that could drift from the lines.
    applied_by_promotion: dict[uuid.UUID, AppliedPromotionResult] = {}
    for item in items:
        if item.promotion_id is None or item.promo_discount <= 0:
            continue
        if item.promotion_id in applied_by_promotion:
            applied_by_promotion[item.promotion_id].discount_amount += item.promo_discount
        else:
            applied_by_promotion[item.promotion_id] = AppliedPromotionResult(
                promotion_id=item.promotion_id,
                name=item.promotion_name_snapshot or "",
                discount_amount=item.promo_discount,
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
                promo_discount=i.promo_discount,
                promotion_name=i.promotion_name_snapshot,
                is_free_gift=i.is_free_gift,
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
        applied_promotions=list(applied_by_promotion.values()),
        warnings=warnings or [],
    )
