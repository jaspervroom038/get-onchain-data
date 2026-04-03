"""Google Sheets sync via Google Sheets API with service account credentials.

Setup:
1. Create a Google Cloud project: https://console.cloud.google.com
2. Enable Google Sheets API and Google Drive API
3. Create a Service Account (IAM & Admin → Service Accounts → Create Service Account)
4. Create a JSON key for the service account (Credentials tab → Add Key → JSON)
5. Download the JSON file and save it (e.g., ./credentials.json)
6. Create a Google Sheet and note its Spreadsheet ID (in the URL)
7. Share the Sheet with the service account email (found in the JSON)
8. Set GOOGLE_SHEETS_CREDENTIALS_PATH and GOOGLE_SHEETS_SPREADSHEET_ID in .env
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import gspread
from google.oauth2.service_account import Credentials

from config import settings
from storage import get_history

logger = logging.getLogger(__name__)

_REALIZED_SYMBOL = "BTC.REALIZED_PRICE"
_BALANCED_SYMBOL = "BTC.BALANCED_PRICE_EST"
_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _get_client() -> Optional[gspread.Spreadsheet]:
    """Load credentials from JSON and authenticate with Google Sheets API.
    
    Returns None if credentials are not configured.
    """
    cred_path = settings.google_sheets_credentials_path
    sheet_id = settings.google_sheets_spreadsheet_id
    
    if not cred_path or not sheet_id:
        return None
    
    try:
        cred_file = Path(cred_path)
        if not cred_file.exists():
            logger.warning("Credentials file not found: %s", cred_path)
            return None
        
        creds = Credentials.from_service_account_file(str(cred_file), scopes=_SCOPES)
        gc = gspread.Client(auth=creds)
        return gc.open_by_key(sheet_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to authenticate with Google Sheets: %s", exc)
        return None


def _ensure_headers(sheet: Any) -> None:
    """Ensure the sheet has headers. Create them if the sheet is empty."""
    try:
        if sheet.row_count == 0 or (sheet.row_count == 1 and not sheet.cell(1, 1).value):
            sheet.insert_row(
                ["date", "day", "month", "year", "realized_price", "balanced_price_est"],
                index=1,
            )
            logger.info("Initialized sheet headers")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to ensure headers: %s", exc)


def _ts_to_row(ts: int, realized: float, balanced: float) -> list:
    """Convert timestamp and prices to a sheet row."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return [
        dt.strftime("%Y-%m-%d"),          # date
        dt.day,                            # day
        dt.month,                          # month
        dt.year,                           # year
        round(realized, 6),                # realized_price
        round(balanced, 6),                # balanced_price_est
    ]


async def sync_sheets_latest(ts: int, metrics: dict[str, float]) -> None:
    """Push the most recent Realized / Balanced data point to Google Sheets."""
    realized = metrics.get(_REALIZED_SYMBOL)
    balanced = metrics.get(_BALANCED_SYMBOL)
    
    if realized is None or balanced is None:
        return
    
    def _do_sync():
        spreadsheet = _get_client()
        if not spreadsheet:
            return
        
        try:
            sheet = spreadsheet.worksheet(settings.google_sheets_sheet_name)
        except gspread.WorksheetNotFound:
            # Create the sheet if it doesn't exist
            sheet = spreadsheet.add_worksheet(
                title=settings.google_sheets_sheet_name,
                rows=1000,
                cols=6,
            )
            logger.info("Created new worksheet: %s", settings.google_sheets_sheet_name)
        
        _ensure_headers(sheet)
        row = _ts_to_row(ts, realized, balanced)
        
        try:
            sheet.append_row(row)
            logger.info("Sheets: appended row for %s", row[0])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to append row to sheets: %s", exc)
    
    # Run synchronous gspread operations in a thread pool
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _do_sync)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Sheets sync failed: %s", exc)


async def sync_sheets_backfill() -> None:
    """Load all stored history and push every day's data to Google Sheets.
    
    Groups multiple data points on the same calendar date and takes the last
    value for that day (most recent intra-day reading).
    """
    spreadsheet = _get_client()
    if not spreadsheet:
        logger.info("Sheets backfill skipped: no credentials configured")
        return
    
    logger.info("Starting Google Sheets backfill …")
    now = int(time.time())
    realized_rows = await get_history(_REALIZED_SYMBOL, from_ts=0, to_ts=now)
    balanced_rows = await get_history(_BALANCED_SYMBOL, from_ts=0, to_ts=now)
    
    # Index by date string (keeps the latest entry per day)
    def index_by_date(rows: list[dict]) -> dict[str, tuple[int, float]]:
        by_date: dict[str, tuple[int, float]] = {}
        for row in rows:
            ts = int(row["timestamp"])
            dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            existing = by_date.get(dt)
            if existing is None or ts > existing[0]:
                by_date[dt] = (ts, float(row["value"]))
        return by_date
    
    realized_by_date = index_by_date(realized_rows)
    balanced_by_date = index_by_date(balanced_rows)
    
    all_dates = sorted(set(realized_by_date) | set(balanced_by_date))
    rows_to_write: list[list] = []
    for date in all_dates:
        r_entry = realized_by_date.get(date)
        b_entry = balanced_by_date.get(date)
        if r_entry is None or b_entry is None:
            continue
        ts = max(r_entry[0], b_entry[0])
        rows_to_write.append(_ts_to_row(ts, r_entry[1], b_entry[1]))
    
    if not rows_to_write:
        logger.info("Sheets backfill: no overlapping data found")
        return
    
    def _do_backfill():
        try:
            sheet = spreadsheet.worksheet(settings.google_sheets_sheet_name)
        except gspread.WorksheetNotFound:
            sheet = spreadsheet.add_worksheet(
                title=settings.google_sheets_sheet_name,
                rows=max(len(rows_to_write) + 1, 1000),
                cols=6,
            )
            logger.info("Created new worksheet: %s", settings.google_sheets_sheet_name)
        
        _ensure_headers(sheet)
        
        # Append rows in batches to avoid rate limiting
        batch_size = 100
        for i in range(0, len(rows_to_write), batch_size):
            batch = rows_to_write[i : i + batch_size]
            try:
                sheet.append_rows(batch)
                logger.info("Sheets backfill: appended batch of %d rows", len(batch))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to append batch to sheets: %s", exc)
    
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _do_backfill)
        logger.info("Sheets backfill complete: %d days pushed", len(rows_to_write))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Sheets backfill failed: %s", exc)
