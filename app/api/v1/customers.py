import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_

from app.core.deps import require
from app.core.permissions import Permission
from app.core.tenancy import TenantContext
from app.models.customer import Customer
from app.schemas.customer import CustomerCreateRequest, CustomerResponse, CustomerUpdateRequest
from app.services import customer_service

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("", response_model=list[CustomerResponse])
async def list_customers(
    q: str | None = None,
    ctx: TenantContext = Depends(require(Permission.manage_customers)),
) -> list[Customer]:
    query = ctx.scoped(Customer)
    if q:
        pattern = f"%{q}%"
        query = query.where(or_(Customer.name.ilike(pattern), Customer.phone.ilike(pattern)))
    result = await ctx.db.scalars(query.order_by(Customer.name))
    return list(result)


@router.get("/{customer_id}", response_model=CustomerResponse)
async def get_customer(
    customer_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.manage_customers)),
) -> Customer:
    customer = await ctx.db.scalar(ctx.scoped(Customer).where(Customer.id == customer_id))
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer not found")
    return customer


@router.post("", response_model=CustomerResponse, status_code=201)
async def create_customer(
    body: CustomerCreateRequest,
    ctx: TenantContext = Depends(require(Permission.manage_customers)),
) -> Customer:
    return await customer_service.create_customer(ctx, body)


@router.patch("/{customer_id}", response_model=CustomerResponse)
async def update_customer(
    customer_id: uuid.UUID,
    body: CustomerUpdateRequest,
    ctx: TenantContext = Depends(require(Permission.manage_customers)),
) -> Customer:
    return await customer_service.update_customer(ctx, customer_id, body)
