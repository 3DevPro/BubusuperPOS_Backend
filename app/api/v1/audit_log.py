from fastapi import APIRouter, Depends, Query

from app.core.deps import require
from app.core.permissions import Permission
from app.core.tenancy import TenantContext
from app.models.audit_log import AuditLog
from app.schemas.audit_log import AuditLogResponse

router = APIRouter(prefix="/audit-log", tags=["audit-log"])


@router.get("", response_model=list[AuditLogResponse])
async def list_audit_log(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(require(Permission.view_audit_log)),
) -> list[AuditLog]:
    result = await ctx.db.scalars(
        ctx.scoped(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)
    )
    return list(result)
