from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException, status

from app.core.tenancy import TenantContext
from app.models.turbo.daily_close import DailyClose, DailyCloseReason
from app.services import audit_service
from app.services.report_service import tenant_local_timezone


async def today_local(ctx: TenantContext) -> date:
    tz = await tenant_local_timezone(ctx)
    return datetime.now(tz).date()


async def close_day(
    ctx: TenantContext,
    business_date: date,
    closed_reason: DailyCloseReason,
    extra_expense: Decimal,
    note: str | None,
) -> DailyClose:
    """Upsert semantics — closing the same business_date twice (e.g. the
    owner corrects the reason later that day) updates the existing row
    instead of erroring, since this is a same-day correction, not a
    conflicting concurrent write."""
    if business_date > await today_local(ctx):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "business_date must not be in the future")

    row = await ctx.db.scalar(ctx.scoped(DailyClose).where(DailyClose.business_date == business_date))
    if row is not None:
        row.closed_reason = closed_reason
        row.extra_expense = extra_expense
        row.note = note
    else:
        row = DailyClose(
            tenant_id=ctx.tenant_id,
            business_date=business_date,
            closed_reason=closed_reason,
            extra_expense=extra_expense,
            note=note,
        )
        ctx.db.add(row)

    await audit_service.record(
        ctx, "daily_close.close", f"ปิดร้านวันที่ {business_date} ({closed_reason.value})"
    )
    await ctx.db.commit()
    await ctx.db.refresh(row)
    return row


async def list_closes(ctx: TenantContext, days: int) -> list[DailyClose]:
    start_date = await today_local(ctx) - timedelta(days=days - 1)
    result = await ctx.db.scalars(
        ctx.scoped(DailyClose)
        .where(DailyClose.business_date >= start_date)
        .order_by(DailyClose.business_date.desc())
    )
    return list(result)
