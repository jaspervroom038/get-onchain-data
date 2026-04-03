"""On-chain metric collectors using free public APIs.

Supported symbols
-----------------
BTC.ACTIVE_ADDRESSES   – estimated number of unique active Bitcoin addresses
BTC.TRANSACTION_COUNT  – number of confirmed Bitcoin transactions (24 h)
BTC.HASH_RATE          – Bitcoin network hash rate (TH/s)
BTC.DIFFICULTY         – current Bitcoin mining difficulty
BTC.FEE_MEDIAN        – median transaction fee in satoshis
ETH.GAS_PRICE          – average Ethereum gas price in Gwei
ETH.TRANSACTION_COUNT  – number of confirmed Ethereum transactions (24 h)
BTC.REALIZED_PRICE     – average on-chain cost basis of the market (USD)
BTC.DELTA_PRICE        – (Realized Cap − Average Cap) / Supply (USD)
BTC.TRANSFERRED_PRICE_EST – estimated transferred price proxy (USD)
BTC.BALANCED_PRICE_EST    – estimated balanced price proxy (USD)
BTC.NUPL               – Net Unrealized Profit/Loss (1 - 1/MVRV)
BTC.PUELL_MULTIPLE     – daily miner revenue / 365-day MA of miner revenue
BTC.MINER_REVENUE      – daily miner revenue (USD)
BTC.FEAR_GREED_INDEX   – Crypto Fear & Greed Index (0-100)
BTC.MA200_RATIO        – BTC Price / 200-day moving average
BTC.PI_CYCLE           – 111-day MA / (2 × 350-day MA)
BTC.EXCHANGE_FLOW_IN   – Daily BTC inflow to exchanges (native units)
BTC.EXCHANGE_FLOW_OUT  – Daily BTC outflow from exchanges (native units)
BTC.EXCHANGE_NET_FLOW  – Net exchange flow (out − in; positive = bullish)
BTC.FEE_TOTAL_USD      – Total daily transaction fees (estimated USD)
BTC.MVRV_ZSCORE        – MVRV Z-Score (standardised deviation from mean)
BTC.ACTIVE_ADDR_CM     – Active addresses from Coin Metrics (AdrActCnt)
"""

import logging
import time
from typing import Any, Optional

import httpx

from storage import get_average_metric, get_daily_sma, get_std_metric, get_history

logger = logging.getLogger(__name__)

# Public APIs used (no API key required for these endpoints)
_BLOCKCHAIN_INFO_STATS = "https://api.blockchain.info/stats"
_BLOCKCHAIR_BTC = "https://api.blockchair.com/bitcoin/stats"
_BLOCKCHAIR_ETH = "https://api.blockchair.com/ethereum/stats"
_COINMETRICS_TIMESERIES = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
_FEAR_GREED_API = "https://api.alternative.me/fng/"

# Seconds to wait before giving up on an HTTP request
_REQUEST_TIMEOUT = 15


async def _get_json(url: str, params: Optional[dict] = None) -> dict:
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()


async def collect_btc_blockchain_info() -> dict[str, float]:
    """Collect Bitcoin metrics from blockchain.info public stats API."""
    data = await _get_json(_BLOCKCHAIN_INFO_STATS)
    return {
        "BTC.ACTIVE_ADDRESSES": float(data.get("n_unique_addresses", 0)),
        "BTC.TRANSACTION_COUNT": float(data.get("n_tx", 0)),
        "BTC.HASH_RATE": float(data.get("hash_rate", 0)),
        "BTC.DIFFICULTY": float(data.get("difficulty", 0)),
    }


async def collect_btc_blockchair() -> dict[str, float]:
    """Collect Bitcoin metrics from Blockchair public stats API."""
    data = await _get_json(_BLOCKCHAIR_BTC)
    stats: dict = data.get("data", {})
    return {
        "BTC.FEE_MEDIAN": float(stats.get("median_transaction_fee_24h", 0)),
        "BTC.TRANSACTION_COUNT": float(stats.get("transactions_24h", 0)),
        "BTC.HASH_RATE": float(stats.get("hashrate_mean", 0)),
        "BTC.DIFFICULTY": float(stats.get("difficulty", 0)),
    }


async def collect_eth_blockchair() -> dict[str, float]:
    """Collect Ethereum metrics from Blockchair public stats API."""
    data = await _get_json(_BLOCKCHAIR_ETH)
    stats: dict = data.get("data", {})
    return {
        "ETH.GAS_PRICE": float(stats.get("average_transaction_fee_24h", 0))
        / 1e9,  # convert Wei → Gwei
        "ETH.TRANSACTION_COUNT": float(stats.get("transactions_24h", 0)),
    }


async def _coinmetrics_latest_row() -> dict[str, Any]:
    """Fetch the latest daily BTC row from Coin Metrics community API."""
    data = await _get_json(
        _COINMETRICS_TIMESERIES,
        params={
            "assets": "btc",
            "metrics": "CapMrktCurUSD,CapMVRVCur,SplyCur,FlowInExNtv,FlowOutExNtv,FeeTotNtv,AdrActCnt",
            "frequency": "1d",
            "limit_per_asset": 1,
        },
    )
    rows = data.get("data", [])
    if not rows:
        raise ValueError("Empty response from Coin Metrics")
    return rows[-1]


async def collect_btc_coinmetrics_pricing() -> dict[str, float]:
    """Collect BTC pricing-model inputs from Coin Metrics and derive metrics.

    Metrics
    -------
    BTC.MARKET_CAP             – Market capitalization (USD)
    BTC.MVRV                   – Market Cap / Realized Cap ratio
    BTC.CIRCULATING_SUPPLY     – circulating supply (BTC)
    BTC.PRICE_USD              – spot proxy from market cap / supply (USD)
    BTC.REALIZED_CAP           – Market Cap / MVRV (USD)
    BTC.AVERAGE_CAP            – running average Market Cap from local DB (USD)
    BTC.REALIZED_PRICE         – Realized Cap / Circulating Supply (USD)
    BTC.DELTA_PRICE            – (Realized Cap − Average Cap) / Supply (USD)
    BTC.BALANCED_PRICE_EST     – estimated balanced price proxy (USD)
    BTC.TRANSFERRED_PRICE_EST  – estimated transferred price proxy (USD)
    BTC.NUPL                   – Net Unrealized Profit/Loss = 1 − 1/MVRV
    BTC.MINER_REVENUE          – daily miner revenue (USD)
    BTC.PUELL_MULTIPLE         – daily revenue / 365-day MA of revenue
    """
    row = await _coinmetrics_latest_row()

    market_cap = float(row["CapMrktCurUSD"])
    mvrv = float(row["CapMVRVCur"])
    supply = float(row["SplyCur"])

    if mvrv <= 0 or supply <= 0:
        raise ValueError("Invalid Coin Metrics inputs for pricing derivation")

    realized_cap = market_cap / mvrv
    btc_price = market_cap / supply
    realized_price = realized_cap / supply

    average_cap = await get_average_metric("BTC.MARKET_CAP")
    if average_cap is None:
        average_cap = market_cap

    delta_price = (realized_cap - average_cap) / supply

    # Heuristic proxy: Transferred Price ≈ Realized Price (closest free proxy),
    # Balanced Price = (Transferred Price + Delta Price) / 2.
    transferred_est = realized_price
    balanced_est = (transferred_est + delta_price) / 2

    # NUPL: Net Unrealized Profit/Loss
    nupl = 1 - (1 / mvrv)

    # Estimated miner revenue: ~144 blocks/day × 3.125 BTC reward × price
    # (post-2024 halving; approximation for the Puell Multiple)
    miner_revenue = 144 * 3.125 * btc_price

    now_ts = int(time.time())
    rev_sma_365 = await get_daily_sma("BTC.MINER_REVENUE", 365, to_ts=now_ts)
    if rev_sma_365 and rev_sma_365 > 0 and miner_revenue > 0:
        puell_multiple = miner_revenue / rev_sma_365
    elif miner_revenue > 0:
        puell_multiple = 1.0  # fallback when insufficient history
    else:
        puell_multiple = 0.0

    result: dict[str, float] = {
        "BTC.MARKET_CAP": market_cap,
        "BTC.MVRV": mvrv,
        "BTC.CIRCULATING_SUPPLY": supply,
        "BTC.PRICE_USD": btc_price,
        "BTC.REALIZED_CAP": realized_cap,
        "BTC.AVERAGE_CAP": average_cap,
        "BTC.REALIZED_PRICE": realized_price,
        "BTC.DELTA_PRICE": delta_price,
        "BTC.BALANCED_PRICE_EST": balanced_est,
        "BTC.TRANSFERRED_PRICE_EST": transferred_est,
        "BTC.NUPL": nupl,
        "BTC.MINER_REVENUE": miner_revenue,
        "BTC.PUELL_MULTIPLE": puell_multiple,
    }

    # Exchange flows (native BTC)
    flow_in_raw = row.get("FlowInExNtv")
    flow_out_raw = row.get("FlowOutExNtv")
    if flow_in_raw and flow_out_raw:
        flow_in = float(flow_in_raw)
        flow_out = float(flow_out_raw)
        result["BTC.EXCHANGE_FLOW_IN"] = flow_in
        result["BTC.EXCHANGE_FLOW_OUT"] = flow_out
        result["BTC.EXCHANGE_NET_FLOW"] = flow_out - flow_in

    # Total transaction fees (native BTC → estimated USD)
    fee_tot_raw = row.get("FeeTotNtv")
    if fee_tot_raw:
        fee_tot_btc = float(fee_tot_raw)
        result["BTC.FEE_TOTAL_USD"] = fee_tot_btc * btc_price

    # Active addresses from Coin Metrics
    adr_act_raw = row.get("AdrActCnt")
    if adr_act_raw:
        result["BTC.ACTIVE_ADDR_CM"] = float(adr_act_raw)

    return result


async def collect_fear_greed() -> dict[str, float]:
    """Collect Fear & Greed Index from alternative.me (free, no key)."""
    data = await _get_json(_FEAR_GREED_API, params={"limit": "1"})
    entries = data.get("data", [])
    if not entries:
        raise ValueError("Empty Fear & Greed response")
    return {"BTC.FEAR_GREED_INDEX": float(entries[0]["value"])}


def _bucket_closes(rows: list[dict[str, float]], bucket_size: int) -> list[float]:
    """Return last close per time bucket from ordered raw rows."""
    buckets: dict[int, float] = {}
    for row in rows:
        bucket = (int(row["timestamp"]) // bucket_size) * bucket_size
        buckets[bucket] = float(row["value"])
    return [buckets[key] for key in sorted(buckets)]


def _compute_wilder_rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """Compute RSI using Wilder smoothing from a series of closes."""
    if len(closes) <= period:
        return None

    deltas = [curr - prev for prev, curr in zip(closes, closes[1:])]
    gains = [max(delta, 0.0) for delta in deltas]
    losses = [max(-delta, 0.0) for delta in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for idx in range(period, len(deltas)):
        avg_gain = ((avg_gain * (period - 1)) + gains[idx]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[idx]) / period

    if avg_loss == 0:
        if avg_gain == 0:
            return 50.0
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


async def collect_btc_mvrv_zscore() -> dict[str, float]:
    """Derive MVRV Z-Score from stored BTC.MVRV history.

    Z = (current_MVRV − mean_MVRV) / std_MVRV
    """
    now_ts = int(time.time())
    rows = await get_history("BTC.MVRV", 0, now_ts)
    if not rows or len(rows) < 30:
        return {}

    current_mvrv = rows[-1]["value"]
    mean_mvrv = await get_average_metric("BTC.MVRV")
    std_mvrv = await get_std_metric("BTC.MVRV")

    if mean_mvrv is None or std_mvrv is None or std_mvrv == 0:
        return {}

    z_score = (current_mvrv - mean_mvrv) / std_mvrv
    return {"BTC.MVRV_ZSCORE": z_score}


async def collect_btc_derived_ma() -> dict[str, float]:
    """Derive moving-average-based metrics from stored BTC.PRICE_USD history.

    BTC.MA200_RATIO – BTC Price / 200-day SMA
    BTC.PI_CYCLE    – 111-day SMA / (2 × 350-day SMA)
    BTC.TWO_YEAR_MA – 730-day moving average
    BTC.TWO_YEAR_MA_X5 – upper band for the classic 2-year MA multiplier
    BTC.MA200W      – 200-week moving average from weekly closes
    BTC.WEEKLY_RSI14 – weekly RSI with Wilder smoothing
    """
    now_ts = int(time.time())
    result: dict[str, float] = {}
    history_start = max(0, now_ts - (230 * 604800))
    rows = await get_history("BTC.PRICE_USD", history_start, now_ts)
    latest_price = float(rows[-1]["value"]) if rows else None

    sma200 = await get_daily_sma("BTC.PRICE_USD", 200, to_ts=now_ts)
    if sma200 and sma200 > 0 and latest_price and latest_price > 0:
        result["BTC.MA200_RATIO"] = latest_price / sma200

    sma111 = await get_daily_sma("BTC.PRICE_USD", 111, to_ts=now_ts)
    sma350 = await get_daily_sma("BTC.PRICE_USD", 350, to_ts=now_ts)
    if sma111 and sma350 and sma350 > 0:
        result["BTC.PI_CYCLE"] = sma111 / (2 * sma350)

    two_year_ma = await get_daily_sma("BTC.PRICE_USD", 730, to_ts=now_ts)
    if two_year_ma and two_year_ma > 0:
        result["BTC.TWO_YEAR_MA"] = two_year_ma
        result["BTC.TWO_YEAR_MA_X5"] = two_year_ma * 5

    if rows:
        weekly_closes = _bucket_closes(rows, 604800)
        if len(weekly_closes) >= 200:
            result["BTC.MA200W"] = sum(weekly_closes[-200:]) / 200

        weekly_rsi = _compute_wilder_rsi(weekly_closes[-60:], period=14)
        if weekly_rsi is not None:
            result["BTC.WEEKLY_RSI14"] = weekly_rsi

    return result


async def collect_all() -> dict[str, float]:
    """Collect all supported on-chain metrics.

    Each source is tried independently so that a single failing API does
    not prevent the others from being collected.
    """
    result: dict[str, float] = {}

    collectors = [
        ("blockchain.info BTC", collect_btc_blockchain_info),
        ("Blockchair BTC", collect_btc_blockchair),
        ("Blockchair ETH", collect_eth_blockchair),
        ("Coin Metrics BTC", collect_btc_coinmetrics_pricing),
        ("Fear & Greed Index", collect_fear_greed),
        ("BTC derived MA", collect_btc_derived_ma),
        ("BTC MVRV Z-Score", collect_btc_mvrv_zscore),
    ]

    for name, collector in collectors:
        try:
            metrics = await collector()
            result.update(metrics)
            logger.info("Collected %d metrics from %s", len(metrics), name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to collect from %s: %s", name, exc)

    return result
