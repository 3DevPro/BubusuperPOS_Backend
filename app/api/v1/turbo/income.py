from fastapi import APIRouter, Depends, Query

from app.core.deps import require
from app.core.permissions import Permission
from app.core.tenancy import TenantContext
from app.schemas.turbo.income import IncomeProfileResponse
from app.services.turbo import income_service

router = APIRouter(prefix="/income-profile", tags=["turbo"])


@router.get("", response_model=IncomeProfileResponse)
async def income_profile(
    days: int = Query(default=30, ge=1, le=90),
    ctx: TenantContext = Depends(require(Permission.view_reports)),
) -> IncomeProfileResponse:
    return await income_service.get_income_profile(ctx, days)
