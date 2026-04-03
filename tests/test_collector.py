"""Tests for the collector module (mocked HTTP calls)."""

from unittest.mock import AsyncMock, patch

import pytest

from collector import collect_all, collect_btc_blockchain_info, collect_btc_blockchair, collect_eth_blockchair, collect_btc_glassnode


BLOCKCHAIN_INFO_RESPONSE = {
    "n_unique_addresses": 900000,
    "n_tx": 350000,
    "hash_rate": 600000000.0,
    "difficulty": 88000000000000.0,
}

BLOCKCHAIR_BTC_RESPONSE = {
    "data": {
        "median_transaction_fee_24h": 2500,
        "transactions_24h": 340000,
        "hashrate_mean": 590000000,
        "difficulty": 87000000000000,
    }
}

BLOCKCHAIR_ETH_RESPONSE = {
    "data": {
        "average_transaction_fee_24h": 5_000_000_000,  # 5 Gwei in Wei
        "transactions_24h": 1200000,
    }
}

# Glassnode returns a list of {t, v} objects – we only care about the last one.
GLASSNODE_REALIZED_PRICE_RESPONSE = [{"t": 1700000000, "v": 30000.0}]
GLASSNODE_BALANCED_PRICE_RESPONSE = [{"t": 1700000000, "v": 18000.0}]
GLASSNODE_DELTA_PRICE_RESPONSE = [{"t": 1700000000, "v": 11500.0}]


@pytest.mark.asyncio
async def test_collect_btc_blockchain_info():
    with patch("collector._get_json", new=AsyncMock(return_value=BLOCKCHAIN_INFO_RESPONSE)):
        metrics = await collect_btc_blockchain_info()

    assert metrics["BTC.ACTIVE_ADDRESSES"] == 900000.0
    assert metrics["BTC.TRANSACTION_COUNT"] == 350000.0
    assert metrics["BTC.HASH_RATE"] == 600000000.0
    assert metrics["BTC.DIFFICULTY"] == 88000000000000.0


@pytest.mark.asyncio
async def test_collect_btc_blockchair():
    with patch("collector._get_json", new=AsyncMock(return_value=BLOCKCHAIR_BTC_RESPONSE)):
        metrics = await collect_btc_blockchair()

    assert metrics["BTC.FEE_MEDIAN"] == 2500.0
    assert metrics["BTC.TRANSACTION_COUNT"] == 340000.0


@pytest.mark.asyncio
async def test_collect_eth_blockchair():
    with patch("collector._get_json", new=AsyncMock(return_value=BLOCKCHAIR_ETH_RESPONSE)):
        metrics = await collect_eth_blockchair()

    assert metrics["ETH.GAS_PRICE"] == pytest.approx(5.0)  # 5_000_000_000 / 1e9
    assert metrics["ETH.TRANSACTION_COUNT"] == 1200000.0


@pytest.mark.asyncio
async def test_collect_all_aggregates_sources():
    async def mock_get_json(url, params=None):
        if "blockchain.info" in url:
            return BLOCKCHAIN_INFO_RESPONSE
        if "blockchair.com/bitcoin" in url:
            return BLOCKCHAIR_BTC_RESPONSE
        if "blockchair.com/ethereum" in url:
            return BLOCKCHAIR_ETH_RESPONSE
        if "glassnode.com" in url:
            if "price_realized" in url:
                return GLASSNODE_REALIZED_PRICE_RESPONSE
            if "balanced_price" in url:
                return GLASSNODE_BALANCED_PRICE_RESPONSE
            if "delta_price" in url:
                return GLASSNODE_DELTA_PRICE_RESPONSE
        return {}

    with patch("collector._get_json", new=mock_get_json), \
         patch("collector.settings") as mock_settings:
        mock_settings.glassnode_api_key = "test-key"
        metrics = await collect_all()

    assert "BTC.ACTIVE_ADDRESSES" in metrics
    assert "BTC.FEE_MEDIAN" in metrics
    assert "ETH.GAS_PRICE" in metrics
    assert "BTC.REALIZED_PRICE" in metrics
    assert "BTC.BALANCED_PRICE" in metrics
    assert "BTC.TRANSFERRED_PRICE" in metrics
    assert "BTC.DELTA_PRICE" in metrics


@pytest.mark.asyncio
async def test_collect_all_tolerates_single_source_failure():
    """collect_all should still return data from working sources."""
    call_count = 0

    async def mock_get_json(url, params=None):
        nonlocal call_count
        call_count += 1
        if "blockchain.info" in url:
            raise RuntimeError("Simulated network error")
        if "blockchair.com/bitcoin" in url:
            return BLOCKCHAIR_BTC_RESPONSE
        if "blockchair.com/ethereum" in url:
            return BLOCKCHAIR_ETH_RESPONSE
        if "glassnode.com" in url:
            if "price_realized" in url:
                return GLASSNODE_REALIZED_PRICE_RESPONSE
            if "balanced_price" in url:
                return GLASSNODE_BALANCED_PRICE_RESPONSE
            if "delta_price" in url:
                return GLASSNODE_DELTA_PRICE_RESPONSE
        return {}

    with patch("collector._get_json", new=mock_get_json), \
         patch("collector.settings") as mock_settings:
        mock_settings.glassnode_api_key = "test-key"
        metrics = await collect_all()

    # blockchain.info failed, but Blockchair and Glassnode still succeeded
    assert "BTC.FEE_MEDIAN" in metrics
    assert "ETH.GAS_PRICE" in metrics
    assert "BTC.REALIZED_PRICE" in metrics


# ---------------------------------------------------------------------------
# Glassnode collector tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collect_btc_glassnode():
    """All four pricing metrics should be returned when Glassnode succeeds."""
    async def mock_get_json(url, params=None):
        if "price_realized" in url:
            return GLASSNODE_REALIZED_PRICE_RESPONSE
        if "balanced_price" in url:
            return GLASSNODE_BALANCED_PRICE_RESPONSE
        if "delta_price" in url:
            return GLASSNODE_DELTA_PRICE_RESPONSE
        return []

    with patch("collector._get_json", new=mock_get_json), \
         patch("collector.settings") as mock_settings:
        mock_settings.glassnode_api_key = "test-key"
        metrics = await collect_btc_glassnode()

    assert metrics["BTC.REALIZED_PRICE"] == 30000.0
    assert metrics["BTC.BALANCED_PRICE"] == 18000.0
    # Transferred = Realized − Balanced
    assert metrics["BTC.TRANSFERRED_PRICE"] == pytest.approx(12000.0)
    assert metrics["BTC.DELTA_PRICE"] == 11500.0


@pytest.mark.asyncio
async def test_collect_btc_glassnode_skips_without_api_key():
    """Should return empty dict when no Glassnode API key is set."""
    with patch("collector.settings") as mock_settings:
        mock_settings.glassnode_api_key = ""
        metrics = await collect_btc_glassnode()

    assert metrics == {}


@pytest.mark.asyncio
async def test_collect_btc_glassnode_partial_failure():
    """Should still return available metrics when some endpoints fail."""
    async def mock_get_json(url, params=None):
        if "price_realized" in url:
            return GLASSNODE_REALIZED_PRICE_RESPONSE
        if "balanced_price" in url:
            raise RuntimeError("Tier too low")
        if "delta_price" in url:
            return GLASSNODE_DELTA_PRICE_RESPONSE
        return []

    with patch("collector._get_json", new=mock_get_json), \
         patch("collector.settings") as mock_settings:
        mock_settings.glassnode_api_key = "test-key"
        metrics = await collect_btc_glassnode()

    assert metrics["BTC.REALIZED_PRICE"] == 30000.0
    assert "BTC.BALANCED_PRICE" not in metrics
    # Transferred cannot be derived without Balanced
    assert "BTC.TRANSFERRED_PRICE" not in metrics
    assert metrics["BTC.DELTA_PRICE"] == 11500.0
