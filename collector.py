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
"""

import logging
from typing import Any, Optional

import httpx

from storage import get_average_metric

logger = logging.getLogger(__name__)

# Public APIs used (no API key required for these endpoints)
_BLOCKCHAIN_INFO_STATS = "https://api.blockchain.info/stats"
_BLOCKCHAIR_BTC = "https://api.blockchair.com/bitcoin/stats"
_BLOCKCHAIR_ETH = "https://api.blockchair.com/ethereum/stats"
_COINMETRICS_TIMESERIES = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"

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
            "metrics": "CapMrktCurUSD,CapMVRVCur,SplyCur",
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

    The Coin Metrics community API is free and provides:
    - Market Cap (CapMrktCurUSD)
    - MVRV (CapMVRVCur)
    - Circulating Supply (SplyCur)

    Derived metrics:
    - Realized Cap = Market Cap / MVRV
    - Realized Price = Realized Cap / Supply
    - Average Cap = running average of BTC.MARKET_CAP in local storage
    - Delta Price = (Realized Cap - Average Cap) / Supply

    Transferred/Balanced are not directly available from free sources,
    so they are exposed as clearly labeled estimates.

    Metrics
    -------
    BTC.MARKET_CAP           – Market capitalization (USD)
    BTC.MVRV                 – Market Cap / Realized Cap ratio
    BTC.CIRCULATING_SUPPLY   – circulating supply (BTC)
    BTC.REALIZED_CAP         – Market Cap / MVRV (USD)
    BTC.AVERAGE_CAP          – running average Market Cap from local DB (USD)
    BTC.REALIZED_PRICE       – Realized Cap / Circulating Supply (USD)
    BTC.DELTA_PRICE          – (Realized Cap − Average Cap) / Supply (USD)
    BTC.BALANCED_PRICE_EST   – estimated balanced price proxy (USD)
    BTC.TRANSFERRED_PRICE_EST – estimated transferred price proxy (USD)
    """
    row = await _coinmetrics_latest_row()

    market_cap = float(row["CapMrktCurUSD"])
    mvrv = float(row["CapMVRVCur"])
    supply = float(row["SplyCur"])

    if mvrv <= 0 or supply <= 0:
        raise ValueError("Invalid Coin Metrics inputs for pricing derivation")

    realized_cap = market_cap / mvrv
    realized_price = realized_cap / supply

    average_cap = await get_average_metric("BTC.MARKET_CAP")
    if average_cap is None:
        average_cap = market_cap

    delta_price = (realized_cap - average_cap) / supply

    # Heuristic proxy: use Delta Price as a balanced-floor estimate.
    balanced_est = delta_price
    transferred_est = realized_price - balanced_est

    return {
        "BTC.MARKET_CAP": market_cap,
        "BTC.MVRV": mvrv,
        "BTC.CIRCULATING_SUPPLY": supply,
        "BTC.REALIZED_CAP": realized_cap,
        "BTC.AVERAGE_CAP": average_cap,
        "BTC.REALIZED_PRICE": realized_price,
        "BTC.DELTA_PRICE": delta_price,
        "BTC.BALANCED_PRICE_EST": balanced_est,
        "BTC.TRANSFERRED_PRICE_EST": transferred_est,
    }


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
    ]

    for name, collector in collectors:
        try:
            metrics = await collector()
            result.update(metrics)
            logger.info("Collected %d metrics from %s", len(metrics), name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to collect from %s: %s", name, exc)

    return result
