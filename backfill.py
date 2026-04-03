"""Historical backfill and incremental daily catch-up.

Modes
-----
Full backfill (``python backfill.py``):
    Fetches ALL history from free APIs, clears existing derived metrics in the
    DB, re-derives everything, and pushes a clean sheet.

Incremental catch-up (``await run_incremental()``):
    Called automatically by the scheduler.  Checks the last stored date for
    BTC.PRICE_USD, fetches only the missing days from Coin Metrics + Fear &
    Greed, derives metrics, and appends to DB + sheet.
"""

import asyncio
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiosqlite
import httpx

from config import settings
from storage import init_db, save_metric

logger = logging.getLogger(__name__)

_COINMETRICS = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
_FEAR_GREED = "https://api.alternative.me/fng/"
_TIMEOUT = 30

# BTC block-reward schedule (halving height → reward in BTC)
_HALVING_SCHEDULE = [
    (0, 50.0),
    (210_000, 25.0),
    (420_000, 12.5),
    (630_000, 6.25),
    (840_000, 3.125),
    (1_050_000, 1.5625),
]

# Approximate block height at known dates for interpolation
_HEIGHT_ANCHORS = [
    (datetime(2009, 1, 3, tzinfo=timezone.utc), 0),
    (datetime(2012, 11, 28, tzinfo=timezone.utc), 210_000),
    (datetime(2016, 7, 9, tzinfo=timezone.utc), 420_000),
    (datetime(2020, 5, 11, tzinfo=timezone.utc), 630_000),
    (datetime(2024, 4, 20, tzinfo=timezone.utc), 840_000),
]


def _estimate_block_height(dt: datetime) -> int:
    """Rough block-height estimate for a given date."""
    ts = dt.timestamp()
    # Find surrounding anchors
    for i in range(len(_HEIGHT_ANCHORS) - 1):
        t0, h0 = _HEIGHT_ANCHORS[i][0].timestamp(), _HEIGHT_ANCHORS[i][1]
        t1, h1 = _HEIGHT_ANCHORS[i + 1][0].timestamp(), _HEIGHT_ANCHORS[i + 1][1]
        if ts <= t1:
            frac = (ts - t0) / (t1 - t0) if t1 != t0 else 0
            return int(h0 + frac * (h1 - h0))
    # After last anchor: ~144 blocks/day
    last_t, last_h = _HEIGHT_ANCHORS[-1]
    days_since = (ts - last_t.timestamp()) / 86400
    return int(last_h + days_since * 144)


def _block_reward(height: int) -> float:
    """BTC block reward at a given height."""
    reward = 50.0
    for h, r in _HALVING_SCHEDULE:
        if height >= h:
            reward = r
    return reward


def _estimate_daily_revenue(dt: datetime, btc_price: float) -> float:
    """Estimate daily miner revenue: ~144 blocks × reward × price."""
    height = _estimate_block_height(dt)
    reward = _block_reward(height)
    return 144 * reward * btc_price


async def _fetch_coinmetrics(start_time: str = "2010-01-01") -> list[dict]:
    """Fetch BTC history from Coin Metrics community API from *start_time*."""
    logger.info("Fetching Coin Metrics data from %s …", start_time)
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(
            _COINMETRICS,
            params={
                "assets": "btc",
                "metrics": "CapMrktCurUSD,CapMVRVCur,SplyCur",
                "frequency": "1d",
                "start_time": start_time,
                "page_size": 10000,
                "sort": "time",
            },
        )
        r.raise_for_status()
        rows = r.json().get("data", [])
    logger.info("Coin Metrics: %d daily rows fetched", len(rows))
    return rows


async def _fetch_fear_greed(limit: str = "0") -> list[dict]:
    """Fetch Fear & Greed history.  *limit* ``"0"`` returns all."""
    logger.info("Fetching Fear & Greed data (limit=%s) …", limit)
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(_FEAR_GREED, params={"limit": limit})
        r.raise_for_status()
        entries = r.json().get("data", [])
    logger.info("Fear & Greed: %d entries fetched", len(entries))
    return entries


def _iso_to_ts(iso: str) -> int:
    """Convert ISO timestamp to unix seconds."""
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


async def run_backfill() -> None:
    db_path = settings.db_path
    await init_db(db_path=db_path)

    # ── 1. Check existing data ──
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM metrics WHERE symbol = 'BTC.PRICE_USD'")
        existing = (await cursor.fetchone())[0]
    logger.info("Existing BTC.PRICE_USD rows: %d", existing)

    # ── 2. Fetch Coin Metrics ──
    cm_rows = await _fetch_coinmetrics()
    if not cm_rows:
        logger.error("No Coin Metrics data, aborting")
        return

    # ── 3. Derive all metrics per day and build (symbol, ts, value) tuples ──
    logger.info("Deriving metrics for %d days …", len(cm_rows))

    # Running average of market cap
    market_cap_sum = 0.0
    market_cap_count = 0

    # Price history for SMA calculations
    daily_prices: list[tuple[int, float]] = []  # (ts, price)
    daily_revenues: list[tuple[int, float]] = []  # (ts, revenue)

    inserts: list[tuple[str, int, float]] = []

    for row in cm_rows:
        ts = _iso_to_ts(row["time"])
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)

        cap_raw = row.get("CapMrktCurUSD")
        mvrv_raw = row.get("CapMVRVCur")
        supply_raw = row.get("SplyCur")

        if not cap_raw or not mvrv_raw or not supply_raw:
            continue

        market_cap = float(cap_raw)
        mvrv = float(mvrv_raw)
        supply = float(supply_raw)

        if mvrv <= 0 or supply <= 0 or market_cap <= 0:
            continue

        btc_price = market_cap / supply
        realized_cap = market_cap / mvrv
        realized_price = realized_cap / supply

        # Running average cap
        market_cap_sum += market_cap
        market_cap_count += 1
        average_cap = market_cap_sum / market_cap_count

        delta_price = (realized_cap - average_cap) / supply
        transferred_est = realized_price
        balanced_est = (transferred_est + delta_price) / 2

        nupl = 1 - (1 / mvrv)

        # Estimated miner revenue
        miner_revenue = _estimate_daily_revenue(dt, btc_price)
        daily_prices.append((ts, btc_price))
        daily_revenues.append((ts, miner_revenue))

        # Puell Multiple: revenue / 365-day SMA of revenue
        puell = 0.0
        if len(daily_revenues) >= 365:
            rev_sma = sum(r for _, r in daily_revenues[-365:]) / 365
            if rev_sma > 0:
                puell = miner_revenue / rev_sma
        elif miner_revenue > 0:
            puell = 1.0  # insufficient history fallback

        # MA200 Ratio
        ma200_ratio = None
        if len(daily_prices) >= 200:
            sma200 = sum(p for _, p in daily_prices[-200:]) / 200
            if sma200 > 0:
                ma200_ratio = btc_price / sma200

        # Pi Cycle: SMA(111) / (2 × SMA(350))
        pi_cycle = None
        if len(daily_prices) >= 350:
            sma111 = sum(p for _, p in daily_prices[-111:]) / 111
            sma350 = sum(p for _, p in daily_prices[-350:]) / 350
            if sma350 > 0:
                pi_cycle = sma111 / (2 * sma350)

        # Collect all inserts for this day
        metrics = {
            "BTC.PRICE_USD": btc_price,
            "BTC.MARKET_CAP": market_cap,
            "BTC.MVRV": mvrv,
            "BTC.CIRCULATING_SUPPLY": supply,
            "BTC.REALIZED_CAP": realized_cap,
            "BTC.AVERAGE_CAP": average_cap,
            "BTC.REALIZED_PRICE": realized_price,
            "BTC.DELTA_PRICE": delta_price,
            "BTC.TRANSFERRED_PRICE_EST": transferred_est,
            "BTC.BALANCED_PRICE_EST": balanced_est,
            "BTC.NUPL": nupl,
            "BTC.MINER_REVENUE": miner_revenue,
            "BTC.PUELL_MULTIPLE": puell,
        }
        if ma200_ratio is not None:
            metrics["BTC.MA200_RATIO"] = ma200_ratio
        if pi_cycle is not None:
            metrics["BTC.PI_CYCLE"] = pi_cycle

        for sym, val in metrics.items():
            if not math.isnan(val) and not math.isinf(val):
                inserts.append((sym, ts, val))

    logger.info("Derived %d metric data points from Coin Metrics", len(inserts))

    # ── 4. Fear & Greed Index ──
    fg_entries = await _fetch_fear_greed()
    for entry in fg_entries:
        ts = int(entry["timestamp"])
        val = float(entry["value"])
        inserts.append(("BTC.FEAR_GREED_INDEX", ts, val))

    logger.info("Total data points to insert: %d", len(inserts))

    # ── 5. Clear existing historical data and insert fresh ──
    async with aiosqlite.connect(db_path) as db:
        # Remove old data for symbols we're backfilling
        symbols_to_clear = set(s for s, _, _ in inserts)
        for sym in symbols_to_clear:
            await db.execute("DELETE FROM metrics WHERE symbol = ?", (sym,))
        await db.commit()
        logger.info("Cleared %d symbols from DB", len(symbols_to_clear))

        # Bulk insert
        await db.executemany(
            "INSERT INTO metrics (symbol, timestamp, value) VALUES (?, ?, ?)",
            inserts,
        )
        await db.commit()
        logger.info("Inserted %d rows into DB", len(inserts))

    # ── 6. Push to Google Sheets ──
    from sheets_sync import sync_sheets_backfill

    logger.info("Pushing to Google Sheets …")
    await sync_sheets_backfill()
    logger.info("Backfill complete!")


async def run_incremental() -> int:
    """Fetch only the days missing since the last stored BTC.PRICE_USD row.

    Returns the number of new data-points inserted (0 when already up-to-date).
    """
    db_path = settings.db_path
    await init_db(db_path=db_path)

    # ── 1. Determine last stored date ──────────────────────────────
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT MAX(timestamp) FROM metrics WHERE symbol = 'BTC.PRICE_USD'"
        )
        row = await cur.fetchone()
        last_ts: Optional[int] = row[0] if row and row[0] else None

    if last_ts is None:
        logger.warning("No existing BTC.PRICE_USD data – running full backfill instead")
        await run_backfill()
        return -1  # signal that a full backfill ran

    last_date = datetime.fromtimestamp(last_ts, tz=timezone.utc)
    next_day = last_date + timedelta(days=1)
    start_iso = next_day.strftime("%Y-%m-%d")
    logger.info("Last stored date: %s – fetching from %s", last_date.date(), start_iso)

    # ── 2. Fetch new Coin Metrics rows ─────────────────────────────
    cm_rows = await _fetch_coinmetrics(start_time=start_iso)
    if not cm_rows:
        logger.info("Coin Metrics: no new rows – already up-to-date")

    # ── 3. Fetch new Fear & Greed entries ──────────────────────────
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT MAX(timestamp) FROM metrics WHERE symbol = 'BTC.FEAR_GREED_INDEX'"
        )
        fg_row = await cur.fetchone()
        fg_last_ts: Optional[int] = fg_row[0] if fg_row and fg_row[0] else None

    fg_entries_new: list[dict] = []
    if fg_last_ts is not None:
        days_missing = max(1, int((time.time() - fg_last_ts) / 86400) + 1)
        if days_missing > 1:
            all_fg = await _fetch_fear_greed(limit=str(days_missing))
            for entry in all_fg:
                if int(entry["timestamp"]) > fg_last_ts:
                    fg_entries_new.append(entry)
    else:
        fg_entries_new = await _fetch_fear_greed(limit="0")

    if not cm_rows and not fg_entries_new:
        logger.info("Nothing new to insert – fully up-to-date")
        return 0

    # ── 4. Load historical state from DB for SMA derivation ────────
    async with aiosqlite.connect(db_path) as db:
        # Cumulative market-cap stats (for average_cap)
        cur = await db.execute(
            "SELECT SUM(value), COUNT(*) FROM metrics WHERE symbol = 'BTC.MARKET_CAP'"
        )
        mc_row = await cur.fetchone()
        market_cap_sum = float(mc_row[0]) if mc_row and mc_row[0] else 0.0
        market_cap_count = int(mc_row[1]) if mc_row and mc_row[1] else 0

        # Last 350 prices (for MA200 + Pi Cycle)
        cur = await db.execute(
            "SELECT timestamp, value FROM metrics "
            "WHERE symbol = 'BTC.PRICE_USD' ORDER BY timestamp DESC LIMIT 350"
        )
        price_rows = await cur.fetchall()
        daily_prices: list[tuple[int, float]] = list(reversed(price_rows))

        # Last 365 revenues (for Puell)
        cur = await db.execute(
            "SELECT timestamp, value FROM metrics "
            "WHERE symbol = 'BTC.MINER_REVENUE' ORDER BY timestamp DESC LIMIT 365"
        )
        rev_rows = await cur.fetchall()
        daily_revenues: list[tuple[int, float]] = list(reversed(rev_rows))

    # ── 5. Derive metrics for each new CM day ──────────────────────
    inserts: list[tuple[str, int, float]] = []

    for row in cm_rows:
        ts = _iso_to_ts(row["time"])
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)

        cap_raw = row.get("CapMrktCurUSD")
        mvrv_raw = row.get("CapMVRVCur")
        supply_raw = row.get("SplyCur")
        if not cap_raw or not mvrv_raw or not supply_raw:
            continue

        market_cap = float(cap_raw)
        mvrv = float(mvrv_raw)
        supply = float(supply_raw)
        if mvrv <= 0 or supply <= 0 or market_cap <= 0:
            continue

        btc_price = market_cap / supply
        realized_cap = market_cap / mvrv
        realized_price = realized_cap / supply

        market_cap_sum += market_cap
        market_cap_count += 1
        average_cap = market_cap_sum / market_cap_count

        delta_price = (realized_cap - average_cap) / supply
        transferred_est = realized_price
        balanced_est = (transferred_est + delta_price) / 2
        nupl = 1 - (1 / mvrv)

        miner_revenue = _estimate_daily_revenue(dt, btc_price)
        daily_prices.append((ts, btc_price))
        daily_revenues.append((ts, miner_revenue))

        # Puell
        puell = 0.0
        if len(daily_revenues) >= 365:
            rev_sma = sum(r for _, r in daily_revenues[-365:]) / 365
            if rev_sma > 0:
                puell = miner_revenue / rev_sma
        elif miner_revenue > 0:
            puell = 1.0

        # MA200 Ratio
        ma200_ratio = None
        if len(daily_prices) >= 200:
            sma200 = sum(p for _, p in daily_prices[-200:]) / 200
            if sma200 > 0:
                ma200_ratio = btc_price / sma200

        # Pi Cycle
        pi_cycle = None
        if len(daily_prices) >= 350:
            sma111 = sum(p for _, p in daily_prices[-111:]) / 111
            sma350 = sum(p for _, p in daily_prices[-350:]) / 350
            if sma350 > 0:
                pi_cycle = sma111 / (2 * sma350)

        metrics = {
            "BTC.PRICE_USD": btc_price,
            "BTC.MARKET_CAP": market_cap,
            "BTC.MVRV": mvrv,
            "BTC.CIRCULATING_SUPPLY": supply,
            "BTC.REALIZED_CAP": realized_cap,
            "BTC.AVERAGE_CAP": average_cap,
            "BTC.REALIZED_PRICE": realized_price,
            "BTC.DELTA_PRICE": delta_price,
            "BTC.TRANSFERRED_PRICE_EST": transferred_est,
            "BTC.BALANCED_PRICE_EST": balanced_est,
            "BTC.NUPL": nupl,
            "BTC.MINER_REVENUE": miner_revenue,
            "BTC.PUELL_MULTIPLE": puell,
        }
        if ma200_ratio is not None:
            metrics["BTC.MA200_RATIO"] = ma200_ratio
        if pi_cycle is not None:
            metrics["BTC.PI_CYCLE"] = pi_cycle

        for sym, val in metrics.items():
            if not math.isnan(val) and not math.isinf(val):
                inserts.append((sym, ts, val))

    # Fear & Greed
    for entry in fg_entries_new:
        ts_fg = int(entry["timestamp"])
        val = float(entry["value"])
        inserts.append(("BTC.FEAR_GREED_INDEX", ts_fg, val))

    if not inserts:
        logger.info("No new data points derived")
        return 0

    logger.info("Inserting %d incremental data points", len(inserts))

    # ── 6. Save to DB via save_metric (respects upsert) ───────────
    for sym, ts, val in inserts:
        await save_metric(sym, val, ts, db_path=db_path)

    # ── 7. Sync new rows to Google Sheets ─────────────────────────
    try:
        from sheets_sync import sync_sheets_backfill
        await sync_sheets_backfill()
        logger.info("Incremental sheets sync done")
    except Exception:
        logger.exception("Sheets sync failed (non-fatal)")

    logger.info("Incremental catch-up complete: %d data points", len(inserts))
    return len(inserts)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    if "--incremental" in sys.argv:
        asyncio.run(run_incremental())
    else:
        asyncio.run(run_backfill())
