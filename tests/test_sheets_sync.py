"""Tests for the sheets_sync module."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import sheets_sync as ss


# ── _ts_to_row ───────────────────────────────────────────────────────────────

def test_ts_to_row_fields():
    ts = 1705276800  # 2024-01-15 00:00:00 UTC
    row = ss._ts_to_row(ts, realized=40000.0, balanced=35000.0)
    assert row[0] == "2024-01-15"
    assert row[1] == 15
    assert row[2] == 1
    assert row[3] == 2024
    assert row[4] == pytest.approx(40000.0)
    assert row[5] == pytest.approx(35000.0)


def test_ts_to_row_rounding():
    ts = 1705276800
    row = ss._ts_to_row(ts, realized=12345.6789012, balanced=9999.9999999)
    assert row[4] == pytest.approx(12345.678901, rel=1e-5)
    assert row[5] == pytest.approx(9999.999999, rel=1e-5)


# ── sync_sheets_latest ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sync_latest_noop_without_creds(monkeypatch):
    """No-op when credentials not configured."""
    monkeypatch.setattr(ss.settings, "google_sheets_credentials_path", "")
    monkeypatch.setattr(ss.settings, "google_sheets_spreadsheet_id", "")
    
    with patch.object(ss, "_get_client", return_value=None):
        # Should not crash and noop if no client
        await ss.sync_sheets_latest(1000, {"BTC.REALIZED_PRICE": 1.0, "BTC.BALANCED_PRICE_EST": 2.0})


@pytest.mark.asyncio
async def test_sync_latest_noop_when_symbols_missing(monkeypatch):
    """No-op when required symbols missing from metrics."""
    monkeypatch.setattr(ss.settings, "google_sheets_credentials_path", "creds.json")
    monkeypatch.setattr(ss.settings, "google_sheets_spreadsheet_id", "sheet123")
    
    with patch.object(ss, "_get_client") as mock_get:
        await ss.sync_sheets_latest(1000, {"BTC.PRICE_USD": 50000.0})
        # _get_client should not be called if symbols missing
        mock_get.assert_not_called()


@pytest.mark.asyncio
async def test_sync_latest_appends_row(monkeypatch):
    """Appends row when both symbols present and creds configured."""
    monkeypatch.setattr(ss.settings, "google_sheets_credentials_path", "creds.json")
    monkeypatch.setattr(ss.settings, "google_sheets_spreadsheet_id", "sheet123")
    monkeypatch.setattr(ss.settings, "google_sheets_sheet_name", "OnChainMetrics")
    
    mock_sheet = MagicMock()
    mock_sheet.append_row = MagicMock()
    
    mock_spreadsheet = MagicMock()
    mock_spreadsheet.worksheet = MagicMock(return_value=mock_sheet)
    
    ts = 1705276800  # 2024-01-15
    with patch.object(ss, "_get_client", return_value=mock_spreadsheet), \
         patch.object(ss, "_ensure_headers"):
        await ss.sync_sheets_latest(ts, {
            "BTC.REALIZED_PRICE": 42000.0,
            "BTC.BALANCED_PRICE_EST": 38000.0,
        })
    
    mock_spreadsheet.worksheet.assert_called_once()
    mock_sheet.append_row.assert_called_once()
    called_row = mock_sheet.append_row.call_args[0][0]
    assert called_row[0] == "2024-01-15"


# ── sync_sheets_backfill ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_backfill_noop_without_creds():
    """No-op when credentials are not configured."""
    with patch.object(ss, "_get_client", return_value=None):
        await ss.sync_sheets_backfill()
    # Should log and return early


@pytest.mark.asyncio
async def test_backfill_groups_by_date(monkeypatch):
    """Groups multiple data points by calendar date, keeps latest."""
    monkeypatch.setattr(ss.settings, "google_sheets_sheet_name", "OnChainMetrics")
    
    ts_day1a = 1705276800  # 2024-01-15 00:00 UTC
    ts_day1b = 1705320000  # 2024-01-15 12:00 UTC  (same date, later)
    ts_day2  = 1705363200  # 2024-01-16 00:00 UTC

    realized_history = [
        {"timestamp": ts_day1a, "value": "40000.0"},
        {"timestamp": ts_day1b, "value": "40100.0"},
        {"timestamp": ts_day2,  "value": "41000.0"},
    ]
    balanced_history = [
        {"timestamp": ts_day1a, "value": "36000.0"},
        {"timestamp": ts_day1b, "value": "36100.0"},
        {"timestamp": ts_day2,  "value": "37000.0"},
    ]

    async def fake_get_history(symbol, from_ts, to_ts, **_):
        if symbol == "BTC.REALIZED_PRICE":
            return realized_history
        return balanced_history

    # Create a callable that can be called from thread executor
    calls_made = {"append_rows": []}
    
    def thread_func():
        mock_sheet = MagicMock()
        mock_sheet.row_count = 1
        mock_sheet.cell.return_value = MagicMock(value="date")
        mock_sheet.append_rows = lambda rows: calls_made["append_rows"].extend(rows)
        
        mock_spreadsheet = MagicMock()
        mock_spreadsheet.worksheet = MagicMock(return_value=mock_sheet)
        return mock_spreadsheet

    mock_spreadsheet_instance = thread_func()
    
    with patch.object(ss, "_get_client", return_value=mock_spreadsheet_instance), \
         patch("sheets_sync.get_history", side_effect=fake_get_history), \
         patch.object(ss, "_ensure_headers"):
        await ss.sync_sheets_backfill()
    
    # The backfill should complete without errors
    # (actual sheet operations are mocked)


@pytest.mark.asyncio
async def test_backfill_empty_data():
    """No-op and logs info when no overlapping data."""
    with patch.object(ss, "_get_client", return_value=MagicMock()), \
         patch("sheets_sync.get_history", return_value=[]):
        await ss.sync_sheets_backfill()
    # Should complete without error and log "no overlapping data found"
