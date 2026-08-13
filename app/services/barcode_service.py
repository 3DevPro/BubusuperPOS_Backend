"""In-store EAN-13 barcode generation for products that don't already have
a manufacturer barcode. Codes start with "20" — the GS1 in-store/restricted-
circulation prefix (20-29) — so a generated code can never collide with a
real retail barcode, and mobile_scanner on the Flutter side already scans
EAN-13 for free."""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import update

from app.core.tenancy import TenantContext
from app.models.product import Product
from app.models.tenant import Tenant

_IN_STORE_PREFIX = "20"


def ean13_check_digit(digits12: str) -> str:
    """digits12 is the 12-digit payload (prefix + counter); returns the
    single check digit that makes it a valid EAN-13. Weights alternate 1/3
    from the left, per the GS1 standard."""
    if len(digits12) != 12 or not digits12.isdigit():
        raise ValueError("digits12 must be exactly 12 digits")
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits12))
    return str((10 - (total % 10)) % 10)


async def allocate_internal_barcode(ctx: TenantContext) -> str:
    """Gap-free per-tenant counter, same row-locked UPDATE...RETURNING
    pattern as Sale's receipt_no and PurchaseOrder's po_no — two concurrent
    allocations for the same tenant can never collide."""
    result = await ctx.db.execute(
        update(Tenant)
        .where(Tenant.id == ctx.tenant_id)
        .values(internal_barcode_counter=Tenant.internal_barcode_counter + 1)
        .returning(Tenant.internal_barcode_counter)
    )
    counter = result.scalar_one()
    payload = f"{_IN_STORE_PREFIX}{counter:010d}"
    return payload + ean13_check_digit(payload)


async def assign_barcode(ctx: TenantContext, product_id: uuid.UUID) -> Product:
    """Idempotent — a product that already has a barcode is returned
    unchanged rather than being given a second one, so retrying (or bulk-
    assigning over a mixed selection) is always safe."""
    product = await ctx.db.scalar(ctx.scoped(Product).where(Product.id == product_id))
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "product not found")
    if product.barcode:
        return product

    product.barcode = await allocate_internal_barcode(ctx)
    await ctx.db.commit()
    await ctx.db.refresh(product)
    return product


async def assign_barcodes_bulk(ctx: TenantContext, product_ids: list[uuid.UUID]) -> list[Product]:
    products = []
    for product_id in product_ids:
        products.append(await assign_barcode(ctx, product_id))
    return products
