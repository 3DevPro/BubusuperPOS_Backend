import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import TenantContext
from app.models.audit_log import AuditLog
from app.models.user import User


async def record(ctx: TenantContext, action: str, summary: str) -> None:
    """Same signature as always — now a thin delegate so AuditLog rows are
    only ever constructed in one place. Doesn't commit, same as before."""
    await record_external(ctx.db, ctx.tenant_id, ctx.user_id, action, summary)


async def record_external(
    db: AsyncSession, tenant_id: uuid.UUID, actor_user_id: uuid.UUID, action: str, summary: str
) -> None:
    """For an actor who has rights over a tenant's data without being a user
    *of* that tenant — today that's only a Branch Champion reviewing a
    LoanApplication (see app/core/branch_scope.py). tenant_id must come from
    the row being acted on, never from the caller's own token: the caller
    already proved they may touch that row by matching assigned_branch_id
    against it first. Doesn't commit, same as record()."""
    actor_name = await db.scalar(select(User.name).where(User.id == actor_user_id))
    db.add(
        AuditLog(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            actor_name=actor_name or "-",
            action=action,
            summary=summary,
        )
    )
