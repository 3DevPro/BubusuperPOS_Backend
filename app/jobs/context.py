"""Builds a TenantContext for background jobs, which have no authenticated
request to derive one from. Every service function a job calls (report_
service, inventory_service, notification_service) only reads ctx.db /
ctx.tenant_id — or, for notification_service.create's optional user_id,
treats None as "the whole tenant" — so a synthetic owner-scoped context is
safe to use for every one of these read-mostly, notification-emitting call
sites."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import TenantContext
from app.models.tenant import Tenant
from app.models.user import User, UserRole


async def all_tenant_ids(db: AsyncSession) -> list[uuid.UUID]:
    return list(await db.scalars(select(Tenant.id)))


async def system_context(db: AsyncSession, tenant_id: uuid.UUID) -> TenantContext | None:
    """None if the tenant has no owner user to attribute the context to —
    shouldn't happen in practice (signup always creates one), but a job must
    not crash the whole sweep over one malformed tenant."""
    owner_id = await db.scalar(
        select(User.id).where(User.tenant_id == tenant_id, User.role == UserRole.owner).limit(1)
    )
    if owner_id is None:
        return None
    return TenantContext(db=db, tenant_id=tenant_id, user_id=owner_id, role=UserRole.owner)
