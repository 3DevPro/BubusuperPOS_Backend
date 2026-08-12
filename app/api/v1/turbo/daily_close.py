from datetime import date

from fastapi import APIRouter, Depends, Query

from app.core.deps import require
from app.core.permissions import Permission
from app.core.tenancy import TenantContext
from app.models.turbo.daily_close import DailyClose
from app.schemas.turbo.daily_close import DailyCloseCreateRequest, DailyCloseResponse
from app.services.turbo import daily_close_service

# create_sale is granted to owner/manager/cashier alike — closing out the
# till at the end of a shift is a normal register action any of them does,
# same reasoning as why they all get create_sale itself.
router = APIRouter(prefix="/daily-close", tags=["turbo"])


@router.post("", response_model=DailyCloseResponse, status_code=201)
async def close_day(
    body: DailyCloseCreateRequest,
    ctx: TenantContext = Depends(require(Permission.create_sale)),
) -> DailyClose:
    return await daily_close_service.close_day(
        ctx, body.business_date, body.closed_reason, body.extra_expense, body.note
    )


@router.delete("/{business_date}", status_code=204)
async def reopen_day(
    business_date: date,
    ctx: TenantContext = Depends(require(Permission.create_sale)),
) -> None:
    await daily_close_service.reopen_day(ctx, business_date)


@router.get("", response_model=list[DailyCloseResponse])
async def list_daily_closes(
    days: int = Query(default=30, ge=1, le=90),
    ctx: TenantContext = Depends(require(Permission.create_sale)),
) -> list[DailyClose]:
    return await daily_close_service.list_closes(ctx, days)
