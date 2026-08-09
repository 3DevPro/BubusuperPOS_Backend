import uuid
from dataclasses import dataclass

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class BranchContext:
    """The branch-side counterpart to TenantContext (see app/core/tenancy.py)
    — deliberately a separate type, not a variant of it. A Branch Champion
    isn't a user of any one shop; they see prospects/leads across a branch's
    radius, which is a different scoping axis than tenant_id entirely (see
    app/models/turbo/branch.py). Nothing here should ever be reachable from
    a TenantContext-authenticated request, or vice versa — see
    app/core/deps.py's get_tenant_context/get_branch_context, which reject
    the other token type outright rather than leaving tenant_id/branch_id
    None and letting a query silently scope to nothing."""

    db: AsyncSession
    branch_id: uuid.UUID
    user_id: uuid.UUID

    def scoped(self, model) -> Select:
        return select(model).where(model.branch_id == self.branch_id)
