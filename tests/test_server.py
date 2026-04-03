"""Tests for the FastAPI HTTP server."""

import pytest
from fastapi.testclient import TestClient

from server import app

# Use the context manager so that ASGI lifespan events (startup/shutdown) run,
# which calls init_db() and ensures the metrics table exists.
client = TestClient(app, raise_server_exceptions=True)


def setup_module(_module):
    """Enter the TestClient lifespan context for the whole module."""
    client.__enter__()


def teardown_module(_module):
    """Exit the TestClient lifespan context after all tests in the module."""
    client.__exit__(None, None, None)


def test_udf_config():
    response = client.get("/udf/config")
    assert response.status_code == 200
    data = response.json()
    assert "supported_resolutions" in data
    assert data["supports_search"] is True


def test_dashboard_homepage():
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "On-Chain Metrics Dashboard" in response.text


def test_udf_time():
    response = client.get("/udf/time")
    assert response.status_code == 200
    ts = response.json()
    assert isinstance(ts, int)
    assert ts > 0


def test_udf_search_returns_results():
    response = client.get("/udf/search", params={"query": "BTC"})
    assert response.status_code == 200
    results = response.json()
    assert isinstance(results, list)
    assert len(results) > 0
    symbols = [r["symbol"] for r in results]
    assert any("BTC" in s for s in symbols)


def test_udf_search_empty_query_returns_all():
    response = client.get("/udf/search", params={"query": ""})
    assert response.status_code == 200
    results = response.json()
    # Every result must contain the required UDF search fields
    for item in results:
        assert "symbol" in item
        assert "description" in item
        assert "exchange" in item
    # Empty query should return all known symbols
    from server import SYMBOL_META
    assert len(results) == len(SYMBOL_META)


def test_udf_symbols_known():
    response = client.get("/udf/symbols", params={"symbol": "BTC.HASH_RATE"})
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "BTC.HASH_RATE"
    assert "supported_resolutions" in data


def test_udf_symbols_estimated_pricing_known():
    response = client.get("/udf/symbols", params={"symbol": "BTC.BALANCED_PRICE_EST"})
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "BTC.BALANCED_PRICE_EST"
    assert data["unit"] == "USD"


def test_udf_symbols_unknown():
    response = client.get("/udf/symbols", params={"symbol": "UNKNOWN.SYMBOL"})
    assert response.status_code == 404


def test_udf_history_no_data():
    response = client.get(
        "/udf/history",
        params={
            "symbol": "BTC.HASH_RATE",
            "resolution": "1D",
            "from": 1000,
            "to": 2000,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["s"] == "no_data"


def test_udf_history_unknown_symbol():
    response = client.get(
        "/udf/history",
        params={
            "symbol": "UNKNOWN.SYM",
            "resolution": "1D",
            "from": 1000,
            "to": 2000,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["s"] == "error"


def test_metrics_list():
    response = client.get("/metrics")
    assert response.status_code == 200
    data = response.json()
    assert "symbols" in data
    assert isinstance(data["symbols"], list)


def test_metrics_latest_not_found():
    response = client.get("/metrics/UNKNOWN.SYMBOL")
    assert response.status_code == 404


def test_metrics_history_empty():
    response = client.get(
        "/metrics/BTC.HASH_RATE/history", params={"from": 0, "to": 9999}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["symbol"] == "BTC.HASH_RATE"
    assert data["data"] == []
