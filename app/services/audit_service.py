from sqlalchemy import select

from app.core.tenancy import TenantContext
from app.models.audit_log import AuditLog
from app.models.user import User


async def record(ctx: TenantContext, action: str, summary: str) -> None:
    actor_name = await ctx.db.scalar(select(User.name).where(User.id == ctx.user_id))
    ctx.db.add(
        AuditLog(
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            actor_name=actor_name or "-",
            action=action,
            summary=summary,
        )
    )
