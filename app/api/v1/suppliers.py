import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_

from app.core.deps import require
from app.core.permissions import Permission
from app.core.tenancy import TenantContext
from app.models.supplier import Supplier
from app.schemas.supplier import SupplierCreateRequest, SupplierResponse, SupplierUpdateRequest
from app.services import supplier_service

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


@router.get("", response_model=list[SupplierResponse])
async def list_suppliers(
    q: str | None = None,
    ctx: TenantContext = Depends(require(Permission.adjust_inventory)),
) -> list[Supplier]:
    query = ctx.scoped(Supplier)
    if q:
        pattern = f"%{q}%"
        query = query.where(or_(Supplier.name.ilike(pattern), Supplier.phone.ilike(pattern)))
    result = await ctx.db.scalars(query.order_by(Supplier.name))
    return list(result)


@router.get("/{supplier_id}", response_model=SupplierResponse)
async def get_supplier(
    supplier_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.adjust_inventory)),
) -> Supplier:
    supplier = await ctx.db.scalar(ctx.scoped(Supplier).where(Supplier.id == supplier_id))
    if supplier is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "supplier not found")
    return supplier


@router.post("", response_model=SupplierResponse, status_code=201)
async def create_supplier(
    body: SupplierCreateRequest,
    ctx: TenantContext = Depends(require(Permission.adjust_inventory)),
) -> Supplier:
    return await supplier_service.create_supplier(ctx, body)


@router.patch("/{supplier_id}", response_model=SupplierResponse)
async def update_supplier(
    supplier_id: uuid.UUID,
    body: SupplierUpdateRequest,
    ctx: TenantContext = Depends(require(Permission.adjust_inventory)),
) -> Supplier:
    return await supplier_service.update_supplier(ctx, supplier_id, body)
