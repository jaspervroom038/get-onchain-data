"""One-time historical backfill: fetch all available history from free APIs,
derive metrics, store in SQLite, then push to Google Sheets.

Usage:
    python backfill.py
"""

import asyncio
import logging
import math
import time
from datetime import datetime, timezone

import aiosqlite
import httpx

from config import settings
from storage import init_db

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
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


async def _fetch_coinmetrics_all() -> list[dict]:
    """Fetch full BTC history from Coin Metrics community API."""
    logger.info("Fetching Coin Metrics historical data …")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(
            _COINMETRICS,
            params={
                "assets": "btc",
                "metrics": "CapMrktCurUSD,CapMVRVCur,SplyCur",
                "frequency": "1d",
                "start_time": "2010-01-01",
                "page_size": 10000,
                "sort": "time",
            },
        )
        r.raise_for_status()
        rows = r.json().get("data", [])
    logger.info("Coin Metrics: %d daily rows fetched", len(rows))
    return rows


async def _fetch_fear_greed_all() -> list[dict]:
    """Fetch full Fear & Greed history (limit=0 returns all)."""
    logger.info("Fetching Fear & Greed historical data …")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(_FEAR_GREED, params={"limit": "0"})
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
    cm_rows = await _fetch_coinmetrics_all()
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
    fg_entries = await _fetch_fear_greed_all()
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


if __name__ == "__main__":
    asyncio.run(run_backfill())
