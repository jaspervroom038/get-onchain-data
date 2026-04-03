"""Tests for storage module."""

import pytest
import pytest_asyncio

from storage import get_average_metric, get_daily_sma, get_history, get_std_metric, init_db, list_symbols, save_metric


@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "test.db")


@pytest.mark.asyncio
async def test_init_db_creates_table(tmp_db):
    await init_db(db_path=tmp_db)
    # Should not raise on a second call (idempotent)
    await init_db(db_path=tmp_db)


@pytest.mark.asyncio
async def test_save_and_retrieve_metric(tmp_db):
    await init_db(db_path=tmp_db)
    await save_metric("BTC.HASH_RATE", 123.45, timestamp=1000, db_path=tmp_db)

    rows = await get_history("BTC.HASH_RATE", 0, 2000, db_path=tmp_db)
    assert len(rows) == 1
    assert rows[0]["value"] == pytest.approx(123.45)
    assert rows[0]["timestamp"] == 1000


@pytest.mark.asyncio
async def test_get_history_filters_by_time_range(tmp_db):
    await init_db(db_path=tmp_db)
    for ts, value in [(100, 1.0), (200, 2.0), (300, 3.0)]:
        await save_metric("BTC.DIFFICULTY", value, timestamp=ts, db_path=tmp_db)

    rows = await get_history("BTC.DIFFICULTY", 150, 250, db_path=tmp_db)
    assert len(rows) == 1
    assert rows[0]["value"] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_get_history_returns_empty_for_unknown_symbol(tmp_db):
    await init_db(db_path=tmp_db)
    rows = await get_history("UNKNOWN.SYMBOL", 0, 9999999, db_path=tmp_db)
    assert rows == []


@pytest.mark.asyncio
async def test_list_symbols(tmp_db):
    await init_db(db_path=tmp_db)
    await save_metric("BTC.HASH_RATE", 1.0, timestamp=1, db_path=tmp_db)
    await save_metric("ETH.GAS_PRICE", 2.0, timestamp=2, db_path=tmp_db)
    await save_metric("BTC.HASH_RATE", 3.0, timestamp=3, db_path=tmp_db)

    symbols = await list_symbols(db_path=tmp_db)
    assert sorted(symbols) == ["BTC.HASH_RATE", "ETH.GAS_PRICE"]


@pytest.mark.asyncio
async def test_get_history_ordered_by_timestamp(tmp_db):
    await init_db(db_path=tmp_db)
    # Insert out of order
    await save_metric("BTC.ACTIVE_ADDRESSES", 300.0, timestamp=300, db_path=tmp_db)
    await save_metric("BTC.ACTIVE_ADDRESSES", 100.0, timestamp=100, db_path=tmp_db)
    await save_metric("BTC.ACTIVE_ADDRESSES", 200.0, timestamp=200, db_path=tmp_db)

    rows = await get_history("BTC.ACTIVE_ADDRESSES", 0, 400, db_path=tmp_db)
    timestamps = [r["timestamp"] for r in rows]
    assert timestamps == sorted(timestamps)


@pytest.mark.asyncio
async def test_get_average_metric(tmp_db):
    await init_db(db_path=tmp_db)
    await save_metric("BTC.MARKET_CAP", 100.0, timestamp=1, db_path=tmp_db)
    await save_metric("BTC.MARKET_CAP", 200.0, timestamp=2, db_path=tmp_db)
    await save_metric("BTC.MARKET_CAP", 400.0, timestamp=3, db_path=tmp_db)

    average = await get_average_metric("BTC.MARKET_CAP", db_path=tmp_db)
    assert average == pytest.approx((100.0 + 200.0 + 400.0) / 3)


@pytest.mark.asyncio
async def test_get_average_metric_unknown_symbol(tmp_db):
    await init_db(db_path=tmp_db)
    average = await get_average_metric("UNKNOWN.SYMBOL", db_path=tmp_db)
    assert average is None


@pytest.mark.asyncio
async def test_get_daily_sma(tmp_db):
    """SMA of daily close values over the last N days."""
    await init_db(db_path=tmp_db)
    # Insert 5 days of data, 1 point per day (86400s apart)
    base_ts = 1_700_000_000
    for i in range(5):
        await save_metric("BTC.PRICE_USD", float((i + 1) * 100), timestamp=base_ts + i * 86400, db_path=tmp_db)

    # SMA of last 3 days: values 300, 400, 500 → avg = 400
    sma = await get_daily_sma("BTC.PRICE_USD", 3, to_ts=base_ts + 5 * 86400, db_path=tmp_db)
    assert sma == pytest.approx(400.0)


@pytest.mark.asyncio
async def test_get_daily_sma_insufficient_data(tmp_db):
    """Returns None when fewer than requested days are available."""
    await init_db(db_path=tmp_db)
    await save_metric("BTC.PRICE_USD", 100.0, timestamp=1_700_000_000, db_path=tmp_db)

    sma = await get_daily_sma("BTC.PRICE_USD", 5, to_ts=1_700_100_000, db_path=tmp_db)
    assert sma is None


@pytest.mark.asyncio
async def test_get_std_metric(tmp_db):
    """Standard deviation of stored values."""
    await init_db(db_path=tmp_db)
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    for i, v in enumerate(values):
        await save_metric("TEST.STD", v, timestamp=1000 + i, db_path=tmp_db)

    std = await get_std_metric("TEST.STD", db_path=tmp_db)
    # Population std of [10,20,30,40,50] = sqrt(200) ≈ 14.142
    import math
    mean = 30.0
    expected_std = math.sqrt(sum((x - mean) ** 2 for x in values) / len(values))
    assert std == pytest.approx(expected_std)


@pytest.mark.asyncio
async def test_get_std_metric_insufficient_data(tmp_db):
    """Returns None when fewer than 2 data points."""
    await init_db(db_path=tmp_db)
    await save_metric("TEST.STD", 100.0, timestamp=1000, db_path=tmp_db)
    std = await get_std_metric("TEST.STD", db_path=tmp_db)
    assert std is None
