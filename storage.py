"""SQLite storage for on-chain time-series metrics."""

import aiosqlite
import time
from typing import Optional

from config import settings

# Number of seconds in one day (used for daily SMA calculations)
_DAY_SECONDS = 86400


async def init_db(db_path: Optional[str] = None) -> None:
    """Create the metrics table if it does not exist."""
    path = db_path or settings.db_path
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS metrics (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol    TEXT    NOT NULL,
                timestamp INTEGER NOT NULL,
                value     REAL    NOT NULL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_symbol_ts ON metrics (symbol, timestamp)"
        )
        await db.commit()


async def save_metric(
    symbol: str,
    value: float,
    timestamp: Optional[int] = None,
    db_path: Optional[str] = None,
) -> None:
    """Persist a single metric data-point."""
    path = db_path or settings.db_path
    ts = timestamp if timestamp is not None else int(time.time())
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT INTO metrics (symbol, timestamp, value) VALUES (?, ?, ?)",
            (symbol, ts, value),
        )
        await db.commit()


async def get_history(
    symbol: str,
    from_ts: int,
    to_ts: int,
    db_path: Optional[str] = None,
) -> list[dict]:
    """Return all data-points for *symbol* within [from_ts, to_ts]."""
    path = db_path or settings.db_path
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT timestamp, value
            FROM   metrics
            WHERE  symbol = ?
              AND  timestamp >= ?
              AND  timestamp <= ?
            ORDER  BY timestamp
            """,
            (symbol, from_ts, to_ts),
        )
        rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def list_symbols(db_path: Optional[str] = None) -> list[str]:
    """Return the distinct metric symbols stored in the database."""
    path = db_path or settings.db_path
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute("SELECT DISTINCT symbol FROM metrics ORDER BY symbol")
        rows = await cursor.fetchall()
    return [row[0] for row in rows]


async def get_average_metric(
    symbol: str,
    db_path: Optional[str] = None,
) -> Optional[float]:
    """Return the arithmetic average value for a symbol, or None if empty."""
    path = db_path or settings.db_path
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            "SELECT AVG(value) FROM metrics WHERE symbol = ?",
            (symbol,),
        )
        row = await cursor.fetchone()

    if row is None or row[0] is None:
        return None
    return float(row[0])


async def get_daily_sma(
    symbol: str,
    days: int,
    to_ts: Optional[int] = None,
    db_path: Optional[str] = None,
) -> Optional[float]:
    """Return the simple moving average of the last *days* daily close values.

    Groups raw data points into calendar-day buckets (UTC), takes the last
    value per day, then averages the most recent *days* buckets.  Returns
    ``None`` when fewer than *days* daily values are available.
    """
    path = db_path or settings.db_path
    end = to_ts if to_ts is not None else int(time.time())
    # Fetch enough raw data – use 2× margin for sparse data
    start = end - (days * _DAY_SECONDS * 2)
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            """
            SELECT (timestamp / :day) AS day_bucket,
                   value
            FROM   metrics
            WHERE  symbol = :sym
              AND  timestamp >= :start
              AND  timestamp <= :end
            ORDER  BY timestamp
            """,
            {"sym": symbol, "day": _DAY_SECONDS, "start": start, "end": end},
        )
        rows = await cursor.fetchall()

    if not rows:
        return None

    # Keep last value per day-bucket
    daily: dict[int, float] = {}
    for bucket, value in rows:
        daily[bucket] = value

    sorted_days = sorted(daily.keys())
    if len(sorted_days) < days:
        return None

    recent = sorted_days[-days:]
    return sum(daily[d] for d in recent) / days
