"""Runs every 15 minutes from the scheduler (see app/jobs/scheduler.py). For
each tenant with the daily summary enabled, once local time has passed the
configured send time for today, sends one end-of-day digest — sales,
profit, and best sellers for today, or the shop's own DailyClose reason if
the owner explicitly closed today, so a legitimate day off doesn't read as
a ฿0 alarm."""

from datetime import datetime

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.tenancy import TenantContext
from app.jobs.context import all_tenant_ids, system_context
from app.models.notification import NotificationKind
from app.models.turbo.daily_close import DailyCloseReason
from app.services import notification_service, report_service
from app.services.turbo import daily_close_service

_CLOSE_REASON_LABELS = {
    DailyCloseReason.sick: "ลาป่วย",
    DailyCloseReason.accident: "อุบัติเหตุ",
    DailyCloseReason.holiday: "วันหยุด",
    DailyCloseReason.other: "ปิดร้าน",
}


async def sweep(session_factory: async_sessionmaker) -> None:
    async with session_factory() as db:
        tenant_ids = await all_tenant_ids(db)

    for tenant_id in tenant_ids:
        async with session_factory() as db:
            ctx = await system_context(db, tenant_id)
            if ctx is not None:
                await _sweep_tenant(ctx)


async def _sweep_tenant(ctx: TenantContext) -> None:
    settings = await notification_service.get_settings(ctx)
    if not settings.daily_summary_enabled:
        return

    tz = await report_service.tenant_local_timezone(ctx)
    now_local = datetime.now(tz)
    if now_local.time() < settings.daily_summary_time:
        return
    today = now_local.date()

    closes = await daily_close_service.list_closes(ctx, days=1)
    todays_close = next((c for c in closes if c.business_date == today), None)

    if todays_close is not None and todays_close.closed_reason != DailyCloseReason.open:
        reason_label = _CLOSE_REASON_LABELS.get(todays_close.closed_reason, "ปิดร้าน")
        title = f"วันนี้ร้านปิด ({reason_label})"
        body = todays_close.note or "ไม่มียอดขายเพราะร้านปิดวันนี้"
        payload = {"business_date": today.isoformat(), "closed_reason": todays_close.closed_reason.value}
    else:
        summary = await report_service.get_summary(ctx, "today")
        best_sellers = await report_service.get_best_sellers(ctx, "today", limit=3)
        title = f"สรุปยอดขายวันนี้ ฿{summary.revenue:,.2f}"
        lines = [
            f"ยอดขาย: {summary.revenue:,.2f} บาท",
            f"กำไร: {summary.profit:,.2f} บาท",
            f"จำนวนบิล: {summary.sale_count}",
        ]
        if best_sellers:
            lines.append("สินค้าขายดี: " + ", ".join(f"{b.name} ({b.qty})" for b in best_sellers))
        body = "\n".join(lines)
        payload = {
            "business_date": today.isoformat(),
            "revenue": str(summary.revenue),
            "profit": str(summary.profit),
            "sale_count": summary.sale_count,
        }

    await notification_service.create(
        ctx,
        kind=NotificationKind.daily_summary,
        title=title,
        body=body,
        dedupe_key=f"daily_summary:{today.isoformat()}",
        payload=payload,
    )
