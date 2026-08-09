import uuid

from fastapi import HTTPException, status

from app.core.tenancy import TenantContext
from app.models.supplier import Supplier
from app.schemas.supplier import SupplierCreateRequest, SupplierUpdateRequest
from app.services import audit_service


async def create_supplier(ctx: TenantContext, body: SupplierCreateRequest) -> Supplier:
    supplier = Supplier(tenant_id=ctx.tenant_id, **body.model_dump())
    ctx.db.add(supplier)
    await audit_service.record(ctx, "supplier.create", f"เพิ่มซัพพลายเออร์ {supplier.name}")
    await ctx.db.commit()
    await ctx.db.refresh(supplier)
    return supplier


async def update_supplier(ctx: TenantContext, supplier_id: uuid.UUID, body: SupplierUpdateRequest) -> Supplier:
    supplier = await ctx.db.scalar(ctx.scoped(Supplier).where(Supplier.id == supplier_id))
    if supplier is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "supplier not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(supplier, field, value)

    await audit_service.record(ctx, "supplier.update", f"แก้ไขข้อมูลซัพพลายเออร์ {supplier.name}")
    await ctx.db.commit()
    await ctx.db.refresh(supplier)
    return supplier
