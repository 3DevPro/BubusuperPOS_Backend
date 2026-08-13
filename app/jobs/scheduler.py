"""In-process scheduler for background sweeps — see the module docstrings on
low_stock_job.sweep / daily_summary_job.sweep for what each one does, and
app/core/config.py's scheduler_enabled for why this runs in-process rather
than as a separate compose service or a host crontab.

Safe for exactly one backend replica (the Dockerfile's uvicorn command has
no --workers flag). If the backend is ever scaled out, every table this
touches has a uniqueness constraint that makes a concurrent double-run
harmless (Notification.dedupe_key, LowStockAlertState's per-product
uniqueness, NotificationDelivery's per-recipient uniqueness) — the risk
would be wasted duplicate work, not duplicate notifications."""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core import db as core_db
from app.jobs import daily_summary_job, low_stock_job
from app.services.notification_dispatch import dispatch_pending

logger = logging.getLogger(__name__)

_SWEEP_INTERVAL_MINUTES = 15

JOBS = {
    "low_stock": low_stock_job.sweep,
    "daily_summary": daily_summary_job.sweep,
}

_scheduler: AsyncIOScheduler | None = None


async def run_job(name: str) -> None:
    """The single code path behind both the scheduled ticks below and the
    manual/debug endpoint (POST /notifications/jobs/{job}/run) — which
    doubles as the escape hatch back to a host-crontab trigger if in-process
    scheduling ever proves wrong, with no redesign needed."""
    # Called through the module (not `from app.core.db import
    # get_session_factory`) so tests can monkeypatch
    # app.core.db.get_session_factory to point at the test DB and have it
    # actually take effect here — a direct import binds the function at
    # import time, before any test fixture gets a chance to patch it.
    session_factory = core_db.get_session_factory()
    if name == "dispatch":
        async with session_factory() as db:
            await dispatch_pending(db)
        return
    job = JOBS.get(name)
    if job is None:
        raise ValueError(f"unknown job: {name}")
    await job(session_factory)


async def _run_and_log(name: str) -> None:
    try:
        await run_job(name)
    except Exception:
        logger.exception("scheduled job %s failed", name)


def start() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    scheduler = AsyncIOScheduler()
    for name in (*JOBS.keys(), "dispatch"):
        scheduler.add_job(
            _run_and_log,
            trigger=IntervalTrigger(minutes=_SWEEP_INTERVAL_MINUTES),
            args=[name],
            id=f"{name}_sweep",
            max_instances=1,
            coalesce=True,
        )
    scheduler.start()
    _scheduler = scheduler


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
