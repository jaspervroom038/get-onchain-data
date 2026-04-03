"""Tests for the collector module (mocked HTTP calls)."""

from unittest.mock import AsyncMock, patch

import pytest

from collector import (
    _compute_wilder_rsi,
    collect_all,
    collect_btc_blockchain_info,
    collect_btc_blockchair,
    collect_btc_coinmetrics_pricing,
    collect_btc_derived_ma,
    collect_btc_mvrv_zscore,
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
            "FlowInExNtv": "24000.5",
            "FlowOutExNtv": "29000.3",
            "FeeTotNtv": "12.345",
            "AdrActCnt": "650000",
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

    # Estimated miner revenue: 144 blocks × 3.125 BTC × price
    btc_price = market_cap / supply
    expected_rev = 144 * 3.125 * btc_price
    assert metrics["BTC.MINER_REVENUE"] == pytest.approx(expected_rev)

    # Puell multiple with no SMA history → fallback 1.0
    assert metrics["BTC.PUELL_MULTIPLE"] == pytest.approx(1.0)

    # Exchange flows
    assert metrics["BTC.EXCHANGE_FLOW_IN"] == pytest.approx(24000.5)
    assert metrics["BTC.EXCHANGE_FLOW_OUT"] == pytest.approx(29000.3)
    assert metrics["BTC.EXCHANGE_NET_FLOW"] == pytest.approx(29000.3 - 24000.5)

    # Fees (native BTC × price)
    assert metrics["BTC.FEE_TOTAL_USD"] == pytest.approx(12.345 * btc_price)

    # Active addresses from Coin Metrics
    assert metrics["BTC.ACTIVE_ADDR_CM"] == pytest.approx(650000.0)


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

    btc_price = 1338870647177.839 / 20010500.0
    expected_rev = 144 * 3.125 * btc_price
    assert metrics["BTC.PUELL_MULTIPLE"] == pytest.approx(expected_rev / 40_000_000.0)


@pytest.mark.asyncio
async def test_collect_fear_greed():
    with patch("collector._get_json", new=AsyncMock(return_value=FEAR_GREED_RESPONSE)):
        metrics = await collect_fear_greed()

    assert metrics["BTC.FEAR_GREED_INDEX"] == pytest.approx(25.0)


@pytest.mark.asyncio
async def test_collect_btc_derived_ma():
    """Derived macro metrics are computed from stored price history."""
    async def fake_sma(symbol, days, to_ts=None, db_path=None):
        return {200: 50000.0, 111: 60000.0, 350: 55000.0, 730: 42000.0}.get(days)

    rows = [
        {"timestamp": 604800 * idx, "value": 30000.0 + (idx * 100.0)}
        for idx in range(220)
    ]

    with patch("collector.get_daily_sma", side_effect=fake_sma), \
         patch("collector.get_history", new=AsyncMock(return_value=rows)):
        metrics = await collect_btc_derived_ma()

    latest_price = rows[-1]["value"]
    weekly_closes = [row["value"] for row in rows]

    assert metrics["BTC.MA200_RATIO"] == pytest.approx(latest_price / 50000.0)
    assert metrics["BTC.PI_CYCLE"] == pytest.approx(60000.0 / (2 * 55000.0))
    assert metrics["BTC.TWO_YEAR_MA"] == pytest.approx(42000.0)
    assert metrics["BTC.TWO_YEAR_MA_X5"] == pytest.approx(210000.0)
    assert metrics["BTC.MA200W"] == pytest.approx(sum(weekly_closes[-200:]) / 200)
    assert metrics["BTC.WEEKLY_RSI14"] == pytest.approx(100.0)


def test_compute_wilder_rsi_flat_series_returns_neutral():
    value = _compute_wilder_rsi([100.0] * 20)

    assert value == pytest.approx(50.0)


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
            patch("collector.get_daily_sma", new=AsyncMock(return_value=None)), \
            patch("collector.get_history", new=AsyncMock(return_value=[])), \
            patch("collector.get_std_metric", new=AsyncMock(return_value=None)):
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
    assert "BTC.EXCHANGE_FLOW_IN" in metrics
    assert "BTC.EXCHANGE_NET_FLOW" in metrics
    assert "BTC.FEE_TOTAL_USD" in metrics
    assert "BTC.ACTIVE_ADDR_CM" in metrics


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
            patch("collector.get_daily_sma", new=AsyncMock(return_value=None)), \
            patch("collector.get_history", new=AsyncMock(return_value=[])), \
            patch("collector.get_std_metric", new=AsyncMock(return_value=None)):
        metrics = await collect_all()

    # blockchain.info failed, but other sources still succeeded
    assert "BTC.FEE_MEDIAN" in metrics
    assert "ETH.GAS_PRICE" in metrics
    assert "BTC.REALIZED_PRICE" in metrics


@pytest.mark.asyncio
async def test_collect_btc_mvrv_zscore():
    """MVRV Z-Score is derived from stored MVRV history."""
    # Build fake history: 50 data points with known mean and std
    fake_history = [{"timestamp": i, "value": 1.0 + i * 0.01} for i in range(50)]
    values = [p["value"] for p in fake_history]
    mean_val = sum(values) / len(values)
    var_val = sum((x - mean_val) ** 2 for x in values) / len(values)
    std_val = var_val ** 0.5

    with patch("collector.get_history", new=AsyncMock(return_value=fake_history)), \
         patch("collector.get_average_metric", new=AsyncMock(return_value=mean_val)), \
         patch("collector.get_std_metric", new=AsyncMock(return_value=std_val)):
        metrics = await collect_btc_mvrv_zscore()

    current = fake_history[-1]["value"]
    expected_z = (current - mean_val) / std_val
    assert metrics["BTC.MVRV_ZSCORE"] == pytest.approx(expected_z)


@pytest.mark.asyncio
async def test_collect_btc_mvrv_zscore_insufficient_history():
    """Returns empty when fewer than 30 data points."""
    fake_history = [{"timestamp": i, "value": 1.0} for i in range(10)]

    with patch("collector.get_history", new=AsyncMock(return_value=fake_history)):
        metrics = await collect_btc_mvrv_zscore()

    assert metrics == {}
