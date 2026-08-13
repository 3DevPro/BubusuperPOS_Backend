"""Runs every 15 minutes from the scheduler (see app/jobs/scheduler.py). For
each tenant with low-stock alerts enabled, once local time has passed the
configured send time for today, digests any *newly* low products into a
single notification — "newly low" meaning no LowStockAlertState row yet, or
one older than low_stock_repeat_days. A product that's recovered above its
threshold has its state row deleted outright, so a restock-then-drop-again
re-alerts immediately instead of waiting out the repeat window."""

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.tenancy import TenantContext
from app.jobs.context import all_tenant_ids, system_context
from app.models.notification import LowStockAlertState, Notification, NotificationKind
from app.services import inventory_service, notification_service
from app.services.report_service import tenant_local_timezone


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
    if not settings.low_stock_enabled:
        return

    tz = await tenant_local_timezone(ctx)
    now_local = datetime.now(tz)
    if now_local.time() < settings.low_stock_time:
        return
    today = now_local.date()

    low_products = await inventory_service.list_low_stock(ctx)
    low_ids = {p.id for p in low_products}

    state_rows = list(await ctx.db.scalars(ctx.scoped(LowStockAlertState)))
    state_by_product = {s.product_id: s for s in state_rows}

    for state in state_rows:
        if state.product_id not in low_ids:
            await ctx.db.delete(state)

    newly_low = [
        p
        for p in low_products
        if (state := state_by_product.get(p.id)) is None
        or (today - state.last_alerted_on) >= timedelta(days=settings.low_stock_repeat_days)
    ]

    if not newly_low:
        await ctx.db.commit()
        return

    for product in newly_low:
        state = state_by_product.get(product.id)
        if state is None:
            ctx.db.add(
                LowStockAlertState(
                    tenant_id=ctx.tenant_id,
                    product_id=product.id,
                    last_alerted_on=today,
                    last_alerted_qty=product.stock_qty,
                )
            )
        else:
            state.last_alerted_on = today
            state.last_alerted_qty = product.stock_qty

    body = "\n".join(f"- {p.name}: เหลือ {p.stock_qty} ชิ้น" for p in newly_low)[:1900]
    # A per-day *sequence number*, not just the day — a restock-then-drop-
    # again within the same day (see the module docstring) is a genuinely
    # new digest and must not collide with the day's earlier one just
    # because it happens to list the same product. Two back-to-back sweeps
    # that would otherwise produce the exact same newly-low set never reach
    # here at all: the state-row check above already excludes a product
    # whose last_alerted_on is today, before this key is ever computed.
    todays_digest_count = await ctx.db.scalar(
        select(func.count(Notification.id)).where(
            Notification.tenant_id == ctx.tenant_id,
            Notification.kind == NotificationKind.low_stock,
            Notification.dedupe_key.like(f"low_stock:{today.isoformat()}:%"),
        )
    )
    await notification_service.create(
        ctx,
        kind=NotificationKind.low_stock,
        title=f"สินค้าใกล้หมด {len(newly_low)} รายการ",
        body=body,
        dedupe_key=f"low_stock:{today.isoformat()}:{todays_digest_count}",
        payload={"product_ids": [str(p.id) for p in newly_low], "business_date": today.isoformat()},
    )
