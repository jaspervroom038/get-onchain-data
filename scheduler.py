"""Periodic scheduler that collects on-chain metrics and stores them."""

import asyncio
import logging
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from collector import collect_all
from config import settings
from sheets_sync import sync_sheets_latest
from storage import save_metric

logger = logging.getLogger(__name__)


async def run_collection() -> None:
    """Collect all metrics and persist each one to the database."""
    logger.info("Starting scheduled metric collection …")
    ts = int(time.time())
    try:
        metrics = await collect_all()
    except Exception as exc:  # noqa: BLE001
        logger.error("Metric collection failed: %s", exc)
        return

    for symbol, value in metrics.items():
        try:
            await save_metric(symbol, value, timestamp=ts)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to save metric %s: %s", symbol, exc)

    logger.info("Stored %d metrics (ts=%d)", len(metrics), ts)

    # Sync latest Realized/Balanced data point to Google Sheets (no-op if unconfigured)
    await sync_sheets_latest(ts, metrics)


async def run_daily_catchup() -> None:
    """Run incremental catch-up to fill any missing days."""
    from backfill import run_incremental

    logger.info("Running daily incremental catch-up …")
    try:
        n = await run_incremental()
        logger.info("Daily catch-up finished: %s new data points", n)
    except Exception as exc:  # noqa: BLE001
        logger.error("Daily catch-up failed: %s", exc)


def create_scheduler() -> AsyncIOScheduler:
    """Build and configure the APScheduler instance."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_collection,
        trigger=IntervalTrigger(seconds=settings.collect_interval_seconds),
        id="collect_metrics",
        name="Collect on-chain metrics",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        run_daily_catchup,
        trigger=CronTrigger(hour=1, minute=0),
        id="daily_catchup",
        name="Daily incremental catch-up",
        replace_existing=True,
        max_instances=1,
    )
    return scheduler


async def start_scheduler_and_collect(scheduler: AsyncIOScheduler) -> None:
    """Start the scheduler, run incremental catch-up, then first collection."""
    scheduler.start()
    logger.info(
        "Scheduler started (interval=%ds)", settings.collect_interval_seconds
    )
    # Fill any gaps since the last run before starting regular collection.
    await run_daily_catchup()
    # Run an initial collection right away so data is available immediately.
    await run_collection()


async def run_forever() -> None:
    """Run the scheduler standalone (without the HTTP server)."""
    from storage import init_db

    await init_db()
    scheduler = create_scheduler()
    await start_scheduler_and_collect(scheduler)
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        scheduler.shutdown()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_forever())
