import uuid

from fastapi import HTTPException, status

from app.core.tenancy import TenantContext
from app.models.product import Product
from app.schemas.product import ProductCreateRequest, ProductUpdateRequest
from app.services import audit_service


async def create_product(ctx: TenantContext, body: ProductCreateRequest) -> Product:
    product = Product(tenant_id=ctx.tenant_id, **body.model_dump())
    ctx.db.add(product)
    await audit_service.record(ctx, "product.create", f"เพิ่มสินค้า {product.name}")
    await ctx.db.commit()
    await ctx.db.refresh(product)
    return product


async def update_product(ctx: TenantContext, product_id: uuid.UUID, body: ProductUpdateRequest) -> Product:
    product = await ctx.db.scalar(ctx.scoped(Product).where(Product.id == product_id))
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "product not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(product, field, value)

    await audit_service.record(ctx, "product.update", f"แก้ไขสินค้า {product.name}")
    await ctx.db.commit()
    await ctx.db.refresh(product)
    return product


async def deactivate_product(ctx: TenantContext, product_id: uuid.UUID) -> Product:
    product = await ctx.db.scalar(ctx.scoped(Product).where(Product.id == product_id))
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "product not found")
    product.is_active = False
    await audit_service.record(ctx, "product.delete", f"ปิดการขายสินค้า {product.name}")
    await ctx.db.commit()
    return product
