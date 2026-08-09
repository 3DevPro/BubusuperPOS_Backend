import uuid
from dataclasses import dataclass

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import UserRole


@dataclass
class TenantContext:
    """Carries the authenticated tenant/user for the request. `scoped()` is the
    only sanctioned way to build a query against a tenant-owned table — it makes
    it structurally hard to forget the tenant_id filter."""

    db: AsyncSession
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    role: UserRole

    def scoped(self, model) -> Select:
        return select(model).where(model.tenant_id == self.tenant_id)
