"""Tests for the collector module (mocked HTTP calls)."""

from unittest.mock import AsyncMock, patch

import pytest

from collector import collect_all, collect_btc_blockchain_info, collect_btc_blockchair, collect_eth_blockchair


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
        return {}

    with patch("collector._get_json", new=mock_get_json):
        metrics = await collect_all()

    assert "BTC.ACTIVE_ADDRESSES" in metrics
    assert "BTC.FEE_MEDIAN" in metrics
    assert "ETH.GAS_PRICE" in metrics


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
        return {}

    with patch("collector._get_json", new=mock_get_json):
        metrics = await collect_all()

    # ETH and BTC Blockchair data should still be present
    assert "ETH.GAS_PRICE" in metrics
    assert "BTC.FEE_MEDIAN" in metrics
    # blockchain.info BTC-specific field should be absent (not from blockchair)
    assert "BTC.ACTIVE_ADDRESSES" not in metrics
