"""Tests for the collector module (mocked HTTP calls)."""

from unittest.mock import AsyncMock, patch

import pytest

from collector import (
    collect_all,
    collect_btc_blockchain_info,
    collect_btc_blockchair,
    collect_btc_coinmetrics_pricing,
    collect_btc_derived_ma,
    collect_eth_blockchair,
    collect_fear_greed,
)


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

COINMETRICS_RESPONSE = {
    "data": [
        {
            "asset": "btc",
            "time": "2026-04-02T00:00:00.000000000Z",
            "CapMrktCurUSD": "1338870647177.839",
            "CapMVRVCur": "1.236157058152968281",
            "SplyCur": "20010500.0",
            "RevUSD": "50000000",
        }
    ]
}

FEAR_GREED_RESPONSE = {
    "data": [{"value": "25", "value_classification": "Extreme Fear"}]
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
async def test_collect_btc_coinmetrics_pricing_derives_values():
    with patch("collector._get_json", new=AsyncMock(return_value=COINMETRICS_RESPONSE)), \
         patch("collector.get_average_metric", new=AsyncMock(return_value=1_100_000_000_000.0)), \
         patch("collector.get_daily_sma", new=AsyncMock(return_value=None)):
        metrics = await collect_btc_coinmetrics_pricing()

    market_cap = 1338870647177.839
    mvrv = 1.236157058152968281
    supply = 20010500.0
    realized_cap = market_cap / mvrv
    realized_price = realized_cap / supply
    average_cap = 1_100_000_000_000.0
    delta = (realized_cap - average_cap) / supply

    assert metrics["BTC.MARKET_CAP"] == pytest.approx(market_cap)
    assert metrics["BTC.MVRV"] == pytest.approx(mvrv)
    assert metrics["BTC.CIRCULATING_SUPPLY"] == pytest.approx(supply)
    assert metrics["BTC.PRICE_USD"] == pytest.approx(market_cap / supply)
    assert metrics["BTC.REALIZED_CAP"] == pytest.approx(realized_cap)
    assert metrics["BTC.AVERAGE_CAP"] == pytest.approx(average_cap)
    assert metrics["BTC.REALIZED_PRICE"] == pytest.approx(realized_price)
    assert metrics["BTC.DELTA_PRICE"] == pytest.approx(delta)
    assert metrics["BTC.TRANSFERRED_PRICE_EST"] == pytest.approx(realized_price)
    assert metrics["BTC.BALANCED_PRICE_EST"] == pytest.approx((realized_price + delta) / 2)

    # NUPL = 1 - 1/MVRV
    assert metrics["BTC.NUPL"] == pytest.approx(1 - (1 / mvrv))

    # Miner revenue
    assert metrics["BTC.MINER_REVENUE"] == pytest.approx(50000000.0)

    # Puell multiple with no SMA history → fallback 1.0
    assert metrics["BTC.PUELL_MULTIPLE"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_collect_btc_coinmetrics_pricing_uses_market_cap_as_fallback_average():
    with patch("collector._get_json", new=AsyncMock(return_value=COINMETRICS_RESPONSE)), \
         patch("collector.get_average_metric", new=AsyncMock(return_value=None)), \
         patch("collector.get_daily_sma", new=AsyncMock(return_value=None)):
        metrics = await collect_btc_coinmetrics_pricing()

    assert metrics["BTC.AVERAGE_CAP"] == pytest.approx(metrics["BTC.MARKET_CAP"])


@pytest.mark.asyncio
async def test_collect_btc_coinmetrics_puell_with_sma():
    """Puell multiple uses SMA when available."""
    with patch("collector._get_json", new=AsyncMock(return_value=COINMETRICS_RESPONSE)), \
         patch("collector.get_average_metric", new=AsyncMock(return_value=1_100_000_000_000.0)), \
         patch("collector.get_daily_sma", new=AsyncMock(return_value=40_000_000.0)):
        metrics = await collect_btc_coinmetrics_pricing()

    assert metrics["BTC.PUELL_MULTIPLE"] == pytest.approx(50_000_000.0 / 40_000_000.0)


@pytest.mark.asyncio
async def test_collect_fear_greed():
    with patch("collector._get_json", new=AsyncMock(return_value=FEAR_GREED_RESPONSE)):
        metrics = await collect_fear_greed()

    assert metrics["BTC.FEAR_GREED_INDEX"] == pytest.approx(25.0)


@pytest.mark.asyncio
async def test_collect_btc_derived_ma():
    """MA200 ratio and Pi Cycle are derived from stored SMA values."""
    async def fake_sma(symbol, days, to_ts=None, db_path=None):
        return {200: 50000.0, 111: 60000.0, 350: 55000.0}.get(days)

    with patch("collector.get_daily_sma", side_effect=fake_sma), \
         patch("storage.get_history", new=AsyncMock(return_value=[{"timestamp": 1, "value": 65000.0}])):
        metrics = await collect_btc_derived_ma()

    assert metrics["BTC.MA200_RATIO"] == pytest.approx(65000.0 / 50000.0)
    assert metrics["BTC.PI_CYCLE"] == pytest.approx(60000.0 / (2 * 55000.0))


@pytest.mark.asyncio
async def test_collect_all_aggregates_sources():
    async def mock_get_json(url, params=None):
        if "blockchain.info" in url:
            return BLOCKCHAIN_INFO_RESPONSE
        if "blockchair.com/bitcoin" in url:
            return BLOCKCHAIR_BTC_RESPONSE
        if "blockchair.com/ethereum" in url:
            return BLOCKCHAIR_ETH_RESPONSE
        if "coinmetrics.io" in url:
            return COINMETRICS_RESPONSE
        if "alternative.me" in url:
            return FEAR_GREED_RESPONSE
        return {}

    with patch("collector._get_json", new=mock_get_json), \
         patch("collector.get_average_metric", new=AsyncMock(return_value=1_100_000_000_000.0)), \
         patch("collector.get_daily_sma", new=AsyncMock(return_value=None)):
        metrics = await collect_all()

    assert "BTC.ACTIVE_ADDRESSES" in metrics
    assert "BTC.FEE_MEDIAN" in metrics
    assert "ETH.GAS_PRICE" in metrics
    assert "BTC.REALIZED_PRICE" in metrics
    assert "BTC.PRICE_USD" in metrics
    assert "BTC.DELTA_PRICE" in metrics
    assert "BTC.BALANCED_PRICE_EST" in metrics
    assert "BTC.TRANSFERRED_PRICE_EST" in metrics
    assert "BTC.NUPL" in metrics
    assert "BTC.FEAR_GREED_INDEX" in metrics


@pytest.mark.asyncio
async def test_collect_all_tolerates_single_source_failure():
    """collect_all should still return data from working sources."""

    async def mock_get_json(url, params=None):
        if "blockchain.info" in url:
            raise RuntimeError("Simulated network error")
        if "blockchair.com/bitcoin" in url:
            return BLOCKCHAIR_BTC_RESPONSE
        if "blockchair.com/ethereum" in url:
            return BLOCKCHAIR_ETH_RESPONSE
        if "coinmetrics.io" in url:
            return COINMETRICS_RESPONSE
        if "alternative.me" in url:
            return FEAR_GREED_RESPONSE
        return {}

    with patch("collector._get_json", new=mock_get_json), \
         patch("collector.get_average_metric", new=AsyncMock(return_value=1_100_000_000_000.0)), \
         patch("collector.get_daily_sma", new=AsyncMock(return_value=None)):
        metrics = await collect_all()

    # blockchain.info failed, but other sources still succeeded
    assert "BTC.FEE_MEDIAN" in metrics
    assert "ETH.GAS_PRICE" in metrics
    assert "BTC.REALIZED_PRICE" in metrics
