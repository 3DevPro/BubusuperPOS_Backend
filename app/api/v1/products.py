import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_

from app.ai.product_lookup_provider import ProductLookupProvider
from app.core.deps import get_product_lookup_provider, require
from app.core.permissions import Permission
from app.core.tenancy import TenantContext
from app.models.product import Product
from app.schemas.product import (
    ProductCreateRequest,
    ProductLookupResponse,
    ProductResponse,
    ProductUpdateRequest,
)
from app.services import product_lookup_service, product_service

router = APIRouter(prefix="/products", tags=["products"])


@router.get("", response_model=list[ProductResponse])
async def list_products(
    q: str | None = None,
    include_inactive: bool = False,
    limit: int | None = Query(default=None, ge=1, le=50),
    ctx: TenantContext = Depends(require(Permission.view_products)),
) -> list[Product]:
    query = ctx.scoped(Product)
    if not include_inactive:
        query = query.where(Product.is_active.is_(True))
    if q:
        pattern = f"%{q}%"
        query = query.where(
            or_(Product.name.ilike(pattern), Product.sku.ilike(pattern), Product.barcode.ilike(pattern))
        )
    query = query.order_by(Product.name)
    if limit is not None:
        query = query.limit(limit)
    result = await ctx.db.scalars(query)
    return list(result)


@router.get("/lookup/{barcode}", response_model=ProductLookupResponse)
async def lookup_product_barcode(
    barcode: str,
    ctx: TenantContext = Depends(require(Permission.manage_products)),
    provider: ProductLookupProvider = Depends(get_product_lookup_provider),
) -> ProductLookupResponse:
    return await product_lookup_service.lookup_barcode(ctx, provider, barcode)


@router.get("/{product_id}", response_model=ProductResponse)
async def get_product(
    product_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.view_products)),
) -> Product:
    product = await ctx.db.scalar(ctx.scoped(Product).where(Product.id == product_id, Product.is_active.is_(True)))
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "product not found")
    return product


@router.post("", response_model=ProductResponse, status_code=201)
async def create_product(
    body: ProductCreateRequest,
    ctx: TenantContext = Depends(require(Permission.manage_products)),
) -> Product:
    return await product_service.create_product(ctx, body)


@router.patch("/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: uuid.UUID,
    body: ProductUpdateRequest,
    ctx: TenantContext = Depends(require(Permission.manage_products)),
) -> Product:
    return await product_service.update_product(ctx, product_id, body)


@router.delete("/{product_id}", status_code=204)
async def delete_product(
    product_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.manage_products)),
) -> None:
    await product_service.deactivate_product(ctx, product_id)
