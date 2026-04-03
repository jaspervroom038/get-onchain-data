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
BTC.TRANSFERRED_PRICE  – time- & volume-weighted historical spending price (USD)
BTC.BALANCED_PRICE     – Realized Price − Transferred Price ("fair value") (USD)
BTC.DELTA_PRICE        – (Realized Cap − Average Cap) / Supply (USD)
"""

import logging
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)

# Public APIs used (no API key required for these endpoints)
_BLOCKCHAIN_INFO_STATS = "https://api.blockchain.info/stats"
_BLOCKCHAIR_BTC = "https://api.blockchair.com/bitcoin/stats"
_BLOCKCHAIR_ETH = "https://api.blockchair.com/ethereum/stats"

# Glassnode API (requires API key configured in settings)
_GLASSNODE_BASE = "https://api.glassnode.com/v1/metrics"

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


async def _glassnode_latest(path: str) -> float:
    """Fetch the most recent value from a Glassnode metrics endpoint.

    Glassnode returns ``[{"t": <unix>, "v": <value>}, …]``.
    We take the last element's ``v``.
    """
    data = await _get_json(
        f"{_GLASSNODE_BASE}/{path}",
        params={"a": "BTC", "api_key": settings.glassnode_api_key},
    )
    if not data:
        raise ValueError(f"Empty response from Glassnode {path}")
    return float(data[-1]["v"])


async def collect_btc_glassnode() -> dict[str, float]:
    """Collect Bitcoin on-chain pricing metrics from Glassnode.

    Requires a valid ``GLASSNODE_API_KEY`` in settings.  Returns an empty
    dict when no key is configured so the rest of the collectors keep
    working.

    Metrics
    -------
    BTC.REALIZED_PRICE     – Realized Cap / Circulating Supply (USD)
    BTC.BALANCED_PRICE     – Realized Price − Transferred Price (USD)
    BTC.TRANSFERRED_PRICE  – Realized Price − Balanced Price (USD, derived)
    BTC.DELTA_PRICE        – (Realized Cap − Average Cap) / Supply (USD)
    """
    if not settings.glassnode_api_key:
        logger.debug("Glassnode API key not configured – skipping")
        return {}

    result: dict[str, float] = {}

    # --- Realized Price ---
    realized: Optional[float] = None
    try:
        realized = await _glassnode_latest("market/price_realized_usd")
        result["BTC.REALIZED_PRICE"] = realized
    except Exception as exc:  # noqa: BLE001
        logger.warning("Glassnode BTC.REALIZED_PRICE failed: %s", exc)

    # --- Balanced Price ---
    balanced: Optional[float] = None
    try:
        balanced = await _glassnode_latest("indicators/balanced_price_usd")
        result["BTC.BALANCED_PRICE"] = balanced
    except Exception as exc:  # noqa: BLE001
        logger.warning("Glassnode BTC.BALANCED_PRICE failed: %s", exc)

    # --- Transferred Price (derived: Realized − Balanced) ---
    if realized is not None and balanced is not None:
        result["BTC.TRANSFERRED_PRICE"] = realized - balanced

    # --- Delta Price ---
    try:
        delta = await _glassnode_latest("indicators/delta_price_usd")
        result["BTC.DELTA_PRICE"] = delta
    except Exception as exc:  # noqa: BLE001
        logger.warning("Glassnode BTC.DELTA_PRICE failed: %s", exc)

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
        ("Glassnode BTC", collect_btc_glassnode),
    ]

    for name, collector in collectors:
        try:
            metrics = await collector()
            result.update(metrics)
            logger.info("Collected %d metrics from %s", len(metrics), name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to collect from %s: %s", name, exc)

    return result
