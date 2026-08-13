from datetime import date, timedelta

from fastapi import HTTPException, status
from sqlalchemy import update

from app.core.tenancy import TenantContext
from app.models.product import Product
from app.models.stock import StockMovement
from app.schemas.inventory import StockAdjustRequest, StockAdjustResult
from app.services import audit_service


async def adjust_stock(ctx: TenantContext, body: StockAdjustRequest) -> StockAdjustResult:
    product = await ctx.db.scalar(ctx.scoped(Product).where(Product.id == body.product_id))
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "product not found")

    result = await ctx.db.execute(
        update(Product)
        .where(Product.id == product.id, Product.tenant_id == ctx.tenant_id)
        .values(stock_qty=Product.stock_qty + body.qty_delta)
        .returning(Product.stock_qty)
    )
    new_qty = result.scalar_one()

    ctx.db.add(
        StockMovement(
            tenant_id=ctx.tenant_id,
            product_id=product.id,
            type=body.type,
            qty_delta=body.qty_delta,
            unit_cost=product.cost_price,
            user_id=ctx.user_id,
            note=body.note,
        )
    )
    await audit_service.record(
        ctx, "inventory.adjust", f"ปรับสต็อก {product.name} {body.qty_delta:+d} ({body.type.value})"
    )
    await ctx.db.commit()
    return StockAdjustResult(product_id=product.id, stock_qty=new_qty)


async def list_low_stock(ctx: TenantContext) -> list[Product]:
    result = await ctx.db.scalars(
        ctx.scoped(Product).where(
            Product.track_stock.is_(True),
            Product.is_active.is_(True),
            Product.stock_qty <= Product.low_stock_threshold,
        )
    )
    return list(result)


async def list_expiring_soon(ctx: TenantContext, days: int = 7) -> list[Product]:
    """Includes anything already past its expiry_date too, not just what's
    coming up — an expired item still sitting in stock needs pulling off the
    shelf even more urgently than one that's merely close."""
    cutoff = date.today() + timedelta(days=days)
    result = await ctx.db.scalars(
        ctx.scoped(Product)
        .where(
            Product.is_active.is_(True),
            Product.stock_qty > 0,
            Product.expiry_date.is_not(None),
            Product.expiry_date <= cutoff,
        )
        .order_by(Product.expiry_date)
    )
    return list(result)
