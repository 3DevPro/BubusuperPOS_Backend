import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import or_

from app.ai.product_lookup_provider import ProductLookupProvider
from app.core.deps import get_product_lookup_provider, require
from app.core.permissions import Permission
from app.core.tenancy import TenantContext
from app.models.product import Product
from app.schemas.product import (
    ProductCreateRequest,
    ProductImageUploadResponse,
    ProductLookupResponse,
    ProductResponse,
    ProductUpdateRequest,
)
from app.services import product_lookup_service, product_service

router = APIRouter(prefix="/products", tags=["products"])

# Served back out via the StaticFiles mount in app/main.py at the same
# /api/v1/media prefix used to build the URL below.
_PRODUCT_IMAGE_DIR = Path("media/products")
_MAX_IMAGE_BYTES = 5 * 1024 * 1024
_IMAGE_EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


@router.post("/upload-image", response_model=ProductImageUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_product_image(
    request: Request,
    file: UploadFile = File(...),
    ctx: TenantContext = Depends(require(Permission.manage_products)),
) -> ProductImageUploadResponse:
    extension = _IMAGE_EXTENSIONS.get(file.content_type or "")
    if extension is None:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "only jpeg/png/webp images are accepted")

    contents = await file.read()
    if len(contents) > _MAX_IMAGE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "image must be 5 MB or smaller")

    _PRODUCT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4()}{extension}"
    (_PRODUCT_IMAGE_DIR / filename).write_bytes(contents)

    # Absolute so it's usable as-is wherever image_url is already stored/
    # rendered (Image.network needs a full URL, not a path) — relies on
    # uvicorn's --proxy-headers in prod so this reflects the public domain
    # rather than the internal backend:8000 address behind Caddy.
    image_url = f"{str(request.base_url).rstrip('/')}/api/v1/media/products/{filename}"
    return ProductImageUploadResponse(image_url=image_url)


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
