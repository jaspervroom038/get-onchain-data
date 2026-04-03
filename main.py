"""Entry point: starts the periodic metric collector and the HTTP server."""

import asyncio
import logging

import uvicorn

from config import settings
from scheduler import create_scheduler, start_scheduler_and_collect
from server import app
from sheets_sync import sync_sheets_backfill
from storage import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s – %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    # Initialise the database
    await init_db()

    # Optionally backfill full history to Google Sheets on startup
    if settings.google_sheets_enable_backfill_on_startup:
        await sync_sheets_backfill()

    # Start the scheduler (also runs an immediate first collection)
    scheduler = create_scheduler()
    await start_scheduler_and_collect(scheduler)

    # Run the HTTP server
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    logger.info("Starting HTTP server on %s:%d", settings.host, settings.port)
    try:
        await server.serve()
    finally:
        scheduler.shutdown()
        logger.info("Scheduler stopped")


if __name__ == "__main__":
    asyncio.run(main())
