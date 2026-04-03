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
from storage import get_history, list_symbols

logger = logging.getLogger(__name__)

# Ordered list of all metric symbols that become sheet columns.
METRIC_COLUMNS: list[str] = [
    "BTC.PRICE_USD",
    "BTC.REALIZED_PRICE",
    "BTC.BALANCED_PRICE_EST",
    "BTC.TRANSFERRED_PRICE_EST",
    "BTC.DELTA_PRICE",
    "BTC.MARKET_CAP",
    "BTC.REALIZED_CAP",
    "BTC.AVERAGE_CAP",
    "BTC.MVRV",
    "BTC.NUPL",
    "BTC.CIRCULATING_SUPPLY",
    "BTC.MINER_REVENUE",
    "BTC.PUELL_MULTIPLE",
    "BTC.FEAR_GREED_INDEX",
    "BTC.MA200_RATIO",
    "BTC.PI_CYCLE",
    "BTC.ACTIVE_ADDRESSES",
    "BTC.TRANSACTION_COUNT",
    "BTC.HASH_RATE",
    "BTC.DIFFICULTY",
    "BTC.FEE_MEDIAN",
    "ETH.GAS_PRICE",
    "ETH.TRANSACTION_COUNT",
]

_DATE_COLS = ["date", "day", "month", "year"]
_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _col_letter(n: int) -> str:
    """Convert a 1-based column index to an Excel-style column letter (A, B, …, Z, AA, AB, …)."""
    result = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(ord("A") + rem) + result
    return result


def _all_headers() -> list[str]:
    """Return the full header row."""
    return _DATE_COLS + METRIC_COLUMNS


def _get_client() -> Optional[gspread.Spreadsheet]:
    """Load credentials from JSON and authenticate with Google Sheets API."""
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
    """Ensure the sheet has the current headers. Replace if outdated."""
    headers = _all_headers()
    try:
        first_row = sheet.row_values(1)
        if first_row == headers:
            return
        if not first_row:
            sheet.insert_row(headers, index=1)
            logger.info("Initialized sheet headers (%d columns)", len(headers))
            return
        last_col = _col_letter(len(headers))
        sheet.update(f"A1:{last_col}1", [headers])
        logger.info("Updated sheet headers (%d columns)", len(headers))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to ensure headers: %s", exc)


def _ts_to_row(ts: int, metrics: dict[str, float]) -> list:
    """Convert a timestamp + metrics dict to a full sheet row."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    date_cells = [
        dt.strftime("%Y-%m-%d"),
        dt.day,
        dt.month,
        dt.year,
    ]
    metric_cells = [
        round(metrics[sym], 6) if sym in metrics else ""
        for sym in METRIC_COLUMNS
    ]
    return date_cells + metric_cells


async def sync_sheets_latest(ts: int, metrics: dict[str, float]) -> None:
    """Push the most recent collection to Google Sheets as a single row."""
    if not metrics:
        return

    def _do_sync():
        spreadsheet = _get_client()
        if not spreadsheet:
            return

        try:
            sheet = spreadsheet.worksheet(settings.google_sheets_sheet_name)
        except gspread.WorksheetNotFound:
            sheet = spreadsheet.add_worksheet(
                title=settings.google_sheets_sheet_name,
                rows=1000,
                cols=len(_all_headers()),
            )
            logger.info("Created new worksheet: %s", settings.google_sheets_sheet_name)

        _ensure_headers(sheet)
        row = _ts_to_row(ts, metrics)

        try:
            sheet.append_row(row, value_input_option="RAW")
            logger.info("Sheets: appended row for %s", row[0])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to append row to sheets: %s", exc)

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _do_sync)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Sheets sync failed: %s", exc)


async def sync_sheets_backfill() -> None:
    """Load all stored history and push every day's data to Google Sheets.

    Groups multiple data points on the same calendar date and takes the last
    value for that day.  All metrics present in the local database are
    included as columns.
    """
    spreadsheet = _get_client()
    if not spreadsheet:
        logger.info("Sheets backfill skipped: no credentials configured")
        return

    logger.info("Starting Google Sheets backfill …")

    now = int(time.time())
    symbols = await list_symbols()

    # Build per-symbol date→value maps (latest per calendar day)
    sym_date_map: dict[str, dict[str, tuple[int, float]]] = {}
    all_dates: set[str] = set()

    for symbol in symbols:
        rows = await get_history(symbol, from_ts=0, to_ts=now)
        by_date: dict[str, tuple[int, float]] = {}
        for row in rows:
            ts_val = int(row["timestamp"])
            dt = datetime.fromtimestamp(ts_val, tz=timezone.utc).strftime("%Y-%m-%d")
            existing = by_date.get(dt)
            if existing is None or ts_val > existing[0]:
                by_date[dt] = (ts_val, float(row["value"]))
        sym_date_map[symbol] = by_date
        all_dates.update(by_date.keys())

    if not all_dates:
        logger.info("Sheets backfill: no data found")
        return

    sorted_dates = sorted(all_dates)
    rows_to_write: list[list] = []
    for date_str in sorted_dates:
        max_ts = 0
        day_metrics: dict[str, float] = {}
        for symbol in symbols:
            entry = sym_date_map.get(symbol, {}).get(date_str)
            if entry:
                ts_val, value = entry
                day_metrics[symbol] = value
                if ts_val > max_ts:
                    max_ts = ts_val
        if max_ts > 0:
            rows_to_write.append(_ts_to_row(max_ts, day_metrics))

    if not rows_to_write:
        logger.info("Sheets backfill: no rows to write")
        return

    def _do_backfill():
        try:
            sheet = spreadsheet.worksheet(settings.google_sheets_sheet_name)
        except gspread.WorksheetNotFound:
            sheet = spreadsheet.add_worksheet(
                title=settings.google_sheets_sheet_name,
                rows=max(len(rows_to_write) + 1, 1000),
                cols=len(_all_headers()),
            )
            logger.info("Created new worksheet: %s", settings.google_sheets_sheet_name)

        _ensure_headers(sheet)

        # Clear existing data rows for a clean backfill
        try:
            if sheet.row_count > 1:
                last_col = _col_letter(len(_all_headers()))
                sheet.batch_clear([f"A2:{last_col}"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to clear previous backfill rows: %s", exc)

        batch_size = 100
        for i in range(0, len(rows_to_write), batch_size):
            batch = rows_to_write[i : i + batch_size]
            try:
                sheet.append_rows(batch, value_input_option="RAW")
                logger.info("Sheets backfill: appended batch of %d rows", len(batch))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to append batch to sheets: %s", exc)

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _do_backfill)
        logger.info("Sheets backfill complete: %d days pushed", len(rows_to_write))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Sheets backfill failed: %s", exc)
