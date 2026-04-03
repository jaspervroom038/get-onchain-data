"""Tests for storage module."""

import pytest
import pytest_asyncio

from storage import get_history, init_db, list_symbols, save_metric


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
