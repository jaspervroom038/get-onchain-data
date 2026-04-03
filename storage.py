"""SQLite storage for on-chain time-series metrics."""

import aiosqlite
import time
from typing import Optional

from config import settings


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
