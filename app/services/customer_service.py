import uuid

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError

from app.core.tenancy import TenantContext
from app.models.customer import Customer
from app.schemas.customer import CustomerCreateRequest, CustomerUpdateRequest
from app.services import audit_service


async def create_customer(ctx: TenantContext, body: CustomerCreateRequest) -> Customer:
    customer = Customer(tenant_id=ctx.tenant_id, name=body.name, phone=body.phone or None)
    ctx.db.add(customer)
    try:
        # audit_service.record's own SELECT autoflushes the pending insert
        # above, so the constraint violation can surface there rather than
        # at the explicit commit() below — both must be inside this block.
        await audit_service.record(ctx, "customer.create", f"เพิ่มลูกค้า {customer.name}")
        await ctx.db.commit()
    except IntegrityError:
        # No pre-check for uniqueness — same idiom as the client_uuid race in
        # sales_service/refund_service, just surfaced as a 400 here since
        # (unlike an idempotency key) a duplicate phone has no prior row to
        # transparently return instead.
        await ctx.db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "phone number already registered")
    await ctx.db.refresh(customer)
    return customer


async def update_customer(ctx: TenantContext, customer_id: uuid.UUID, body: CustomerUpdateRequest) -> Customer:
    customer = await ctx.db.scalar(ctx.scoped(Customer).where(Customer.id == customer_id))
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(customer, field, value)

    try:
        await audit_service.record(ctx, "customer.update", f"แก้ไขข้อมูลลูกค้า {customer.name}")
        await ctx.db.commit()
    except IntegrityError:
        await ctx.db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "phone number already registered")
    await ctx.db.refresh(customer)
    return customer
