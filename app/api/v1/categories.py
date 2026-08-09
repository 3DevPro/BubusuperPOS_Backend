import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError

from app.core.deps import require
from app.core.permissions import Permission
from app.core.tenancy import TenantContext
from app.models.product import Category
from app.schemas.category import CategoryCreateRequest, CategoryResponse, CategoryUpdateRequest
from app.services import audit_service

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("", response_model=list[CategoryResponse])
async def list_categories(ctx: TenantContext = Depends(require(Permission.view_products))) -> list[Category]:
    result = await ctx.db.scalars(ctx.scoped(Category).order_by(Category.name))
    return list(result)


@router.post("", response_model=CategoryResponse, status_code=201)
async def create_category(
    body: CategoryCreateRequest,
    ctx: TenantContext = Depends(require(Permission.manage_products)),
) -> Category:
    category = Category(tenant_id=ctx.tenant_id, name=body.name)
    ctx.db.add(category)
    await audit_service.record(ctx, "category.create", f"เพิ่มหมวดหมู่ {category.name}")
    await ctx.db.commit()
    await ctx.db.refresh(category)
    return category


@router.patch("/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: uuid.UUID,
    body: CategoryUpdateRequest,
    ctx: TenantContext = Depends(require(Permission.manage_products)),
) -> Category:
    category = await ctx.db.scalar(ctx.scoped(Category).where(Category.id == category_id))
    if category is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "category not found")
    old_name = category.name
    category.name = body.name
    await audit_service.record(
        ctx, "category.update", f"แก้ไขชื่อหมวดหมู่ {old_name} → {category.name}"
    )
    await ctx.db.commit()
    await ctx.db.refresh(category)
    return category


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.manage_products)),
) -> None:
    category = await ctx.db.scalar(ctx.scoped(Category).where(Category.id == category_id))
    if category is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "category not found")
    await audit_service.record(ctx, "category.delete", f"ลบหมวดหมู่ {category.name}")
    await ctx.db.delete(category)
    try:
        await ctx.db.commit()
    except IntegrityError:
        await ctx.db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "มีสินค้าอยู่ในหมวดหมู่นี้ ย้ายสินค้าออกก่อนแล้วลองใหม่"
        ) from None
