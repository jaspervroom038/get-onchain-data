"""FastAPI server exposing on-chain metrics via a TradingView UDF-compatible
API as well as a simple REST API for generic consumers.

TradingView UDF endpoints
--------------------------
GET /udf/config       – server capabilities
GET /udf/time         – current server unix timestamp
GET /udf/search       – symbol search
GET /udf/symbols      – symbol metadata
GET /udf/history      – historical OHLCV bars (value is used for C; O=H=L=C)

Generic REST endpoints
-----------------------
GET /metrics          – list all known symbols
GET /metrics/{symbol} – latest value for a symbol
GET /metrics/{symbol}/history – time-range history for a symbol
"""

import math
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from storage import get_history, init_db, list_symbols, save_metric

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="On-Chain Crypto Metrics API",
    description=(
        "Periodically collected on-chain crypto metrics exposed via a "
        "TradingView UDF-compatible API and a generic REST API."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Supported symbols and their human-readable descriptions
# ---------------------------------------------------------------------------

SYMBOL_META: dict[str, dict[str, Any]] = {
    "BTC.ACTIVE_ADDRESSES": {
        "description": "Bitcoin Active Addresses",
        "unit": "addresses",
    },
    "BTC.TRANSACTION_COUNT": {
        "description": "Bitcoin Transactions (24 h)",
        "unit": "transactions",
    },
    "BTC.HASH_RATE": {
        "description": "Bitcoin Hash Rate",
        "unit": "TH/s",
    },
    "BTC.DIFFICULTY": {
        "description": "Bitcoin Mining Difficulty",
        "unit": "",
    },
    "BTC.FEE_MEDIAN": {
        "description": "Bitcoin Median Transaction Fee",
        "unit": "satoshis",
    },
    "ETH.GAS_PRICE": {
        "description": "Ethereum Average Gas Price (24 h)",
        "unit": "Gwei",
    },
    "ETH.TRANSACTION_COUNT": {
        "description": "Ethereum Transactions (24 h)",
        "unit": "transactions",
    },
    "BTC.MARKET_CAP": {
        "description": "Bitcoin Market Capitalization",
        "unit": "USD",
    },
    "BTC.MVRV": {
        "description": "Bitcoin MVRV Ratio",
        "unit": "ratio",
    },
    "BTC.CIRCULATING_SUPPLY": {
        "description": "Bitcoin Circulating Supply",
        "unit": "BTC",
    },
    "BTC.REALIZED_CAP": {
        "description": "Bitcoin Realized Capitalization",
        "unit": "USD",
    },
    "BTC.AVERAGE_CAP": {
        "description": "Bitcoin Average Market Cap (running average)",
        "unit": "USD",
    },
    "BTC.REALIZED_PRICE": {
        "description": "Bitcoin Realized Price (avg on-chain cost basis)",
        "unit": "USD",
    },
    "BTC.TRANSFERRED_PRICE_EST": {
        "description": "Bitcoin Transferred Price (estimated proxy)",
        "unit": "USD",
    },
    "BTC.BALANCED_PRICE_EST": {
        "description": "Bitcoin Balanced Price (estimated proxy)",
        "unit": "USD",
    },
    "BTC.DELTA_PRICE": {
        "description": "Bitcoin Delta Price (fundamental/technical floor model)",
        "unit": "USD",
    },
}

# Resolutions advertised to TradingView (in minutes for intraday, D/W/M for daily+)
_SUPPORTED_RESOLUTIONS = ["60", "240", "1D", "1W"]


# ---------------------------------------------------------------------------
# TradingView UDF endpoints
# ---------------------------------------------------------------------------


@app.get("/udf/config", tags=["TradingView UDF"])
async def udf_config() -> dict:
    """Return server capabilities to TradingView."""
    return {
        "supported_resolutions": _SUPPORTED_RESOLUTIONS,
        "supports_group_request": False,
        "supports_marks": False,
        "supports_search": True,
        "supports_timescale_marks": False,
        "exchanges": [{"value": "ONCHAIN", "name": "On-Chain", "desc": "On-Chain Data"}],
        "symbols_types": [{"name": "On-Chain Metric", "value": "metric"}],
    }


@app.get("/udf/time", tags=["TradingView UDF"])
async def udf_time() -> int:
    """Return the current server unix timestamp."""
    return int(time.time())


@app.get("/udf/search", tags=["TradingView UDF"])
async def udf_search(
    query: str = Query("", description="Search query"),
    limit: int = Query(30, ge=1, le=100),
) -> list[dict]:
    """Search for symbols matching the query string."""
    results = []
    q = query.upper()
    for sym, meta in SYMBOL_META.items():
        if q in sym or q in meta["description"].upper():
            results.append(
                {
                    "symbol": sym,
                    "full_name": sym,
                    "description": meta["description"],
                    "exchange": "ONCHAIN",
                    "ticker": sym,
                    "type": "metric",
                }
            )
        if len(results) >= limit:
            break
    return results


@app.get("/udf/symbols", tags=["TradingView UDF"])
async def udf_symbols(symbol: str = Query(..., description="Symbol name")) -> dict:
    """Return metadata for a single symbol."""
    sym = symbol.upper()
    meta = SYMBOL_META.get(sym)
    if meta is None:
        raise HTTPException(status_code=404, detail="Symbol not found")
    return {
        "name": sym,
        "full_name": sym,
        "description": meta["description"],
        "exchange": "ONCHAIN",
        "type": "metric",
        "session": "24x7",
        "timezone": "Etc/UTC",
        "ticker": sym,
        "minmov": 1,
        "pricescale": 100,
        "has_intraday": True,
        "intraday_multipliers": ["60", "240"],
        "has_daily": True,
        "has_weekly_and_monthly": True,
        "supported_resolutions": _SUPPORTED_RESOLUTIONS,
        "volume_precision": 0,
        "unit": meta.get("unit", ""),
    }


@app.get("/udf/history", tags=["TradingView UDF"])
async def udf_history(
    symbol: str = Query(...),
    resolution: str = Query(...),
    from_ts: int = Query(..., alias="from"),
    to_ts: int = Query(..., alias="to"),
) -> dict:
    """Return historical bars for TradingView in UDF format.

    Each data-point is mapped to a bar where ``o == h == l == c == value``
    and ``v == 0`` (volume is not applicable for on-chain metrics).
    The bars are aggregated to the requested resolution by picking the
    *last* value in each bucket.
    """
    sym = symbol.upper()
    if sym not in SYMBOL_META:
        return {"s": "error", "errmsg": f"Unknown symbol: {sym}"}

    rows = await get_history(sym, from_ts, to_ts)
    if not rows:
        return {"s": "no_data"}

    # Resolution → bucket width in seconds
    res_map = {"60": 3600, "240": 14400, "1D": 86400, "1W": 604800}
    bucket_size = res_map.get(resolution, 3600)

    # Aggregate into buckets
    buckets: dict[int, list[float]] = {}
    for row in rows:
        bucket = (row["timestamp"] // bucket_size) * bucket_size
        buckets.setdefault(bucket, []).append(row["value"])

    times = sorted(buckets.keys())
    bars_t, bars_o, bars_h, bars_l, bars_c, bars_v = [], [], [], [], [], []
    for t in times:
        vals = buckets[t]
        open_ = vals[0]
        close_ = vals[-1]
        high_ = max(vals)
        low_ = min(vals)
        if any(math.isnan(v) or math.isinf(v) for v in [open_, close_, high_, low_]):
            continue
        bars_t.append(t)
        bars_o.append(open_)
        bars_h.append(high_)
        bars_l.append(low_)
        bars_c.append(close_)
        bars_v.append(0)

    if not bars_t:
        return {"s": "no_data"}

    return {
        "s": "ok",
        "t": bars_t,
        "o": bars_o,
        "h": bars_h,
        "l": bars_l,
        "c": bars_c,
        "v": bars_v,
    }


# ---------------------------------------------------------------------------
# Generic REST endpoints
# ---------------------------------------------------------------------------


@app.get("/metrics", tags=["Metrics"])
async def list_metric_symbols() -> dict:
    """Return all symbols that have at least one stored data-point."""
    symbols = await list_symbols()
    return {"symbols": symbols}


@app.get("/metrics/{symbol}", tags=["Metrics"])
async def get_latest_metric(symbol: str) -> dict:
    """Return the most recent value for the requested symbol."""
    sym = symbol.upper()
    now = int(time.time())
    rows = await get_history(sym, 0, now)
    if not rows:
        raise HTTPException(status_code=404, detail=f"No data found for {sym}")
    latest = rows[-1]
    return {
        "symbol": sym,
        "timestamp": latest["timestamp"],
        "value": latest["value"],
    }


@app.get("/metrics/{symbol}/history", tags=["Metrics"])
async def get_metric_history(
    symbol: str,
    from_ts: int = Query(0, alias="from", description="Start unix timestamp"),
    to_ts: int = Query(None, alias="to", description="End unix timestamp"),
) -> dict:
    """Return historical data-points for the requested symbol."""
    sym = symbol.upper()
    end = to_ts if to_ts is not None else int(time.time())
    rows = await get_history(sym, from_ts, end)
    return {"symbol": sym, "data": rows}
