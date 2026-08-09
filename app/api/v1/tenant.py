from fastapi import APIRouter, Depends, HTTPException, status

from app.core.deps import CurrentTenant, require
from app.core.permissions import Permission
from app.core.tenancy import TenantContext
from app.models.tenant import Tenant
from app.schemas.tenant import TenantSettingsResponse, TenantSettingsUpdateRequest
from app.services import audit_service

router = APIRouter(prefix="/tenant", tags=["tenant"])


@router.get("/settings", response_model=TenantSettingsResponse)
async def get_settings(ctx: CurrentTenant) -> Tenant:
    tenant = await ctx.db.get(Tenant, ctx.tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tenant not found")
    return tenant


@router.patch("/settings", response_model=TenantSettingsResponse)
async def update_settings(
    body: TenantSettingsUpdateRequest,
    ctx: TenantContext = Depends(require(Permission.manage_settings)),
) -> Tenant:
    tenant = await ctx.db.get(Tenant, ctx.tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tenant not found")

    # Only apply fields the client actually sent — otherwise a PATCH that
    # only touches vat_rate would silently null out promptpay_id and vice
    # versa, since every field here defaults to None.
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(tenant, field, value)

    await audit_service.record(ctx, "tenant_settings.update", "แก้ไขตั้งค่าร้าน")
    await ctx.db.commit()
    await ctx.db.refresh(tenant)
    return tenant
