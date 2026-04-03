"""Fetch, compute and present on-chain Bitcoin pricing metrics.

Metrics
-------
* **Realized Price** – average on-chain cost basis of the market.
* **Transferred Price** – time- & volume-weighted measure of historical spending
  activity.  Derived here as ``Realized Price − Balanced Price``.
* **Balanced Price** – ``Realized Price − Transferred Price``.  Considered a
  "fair-value" floor at the end of bear markets (Glassnode).
* **Delta Price** – ``(Realized Cap − Average Cap) / Circulating Supply``.
  A hybrid fundamental / technical bottom model.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.glassnode_client import GlassnodeClient


def _latest_value(data: list[dict[str, Any]]) -> tuple[datetime, float]:
    """Return the (timestamp, value) of the most recent data point."""
    if not data:
        raise ValueError("Empty data returned from API")
    last = data[-1]
    ts = datetime.fromtimestamp(last["t"], tz=timezone.utc)
    return ts, float(last["v"])


def fetch_metrics(api_key: str | None = None) -> dict[str, Any]:
    """Fetch all four pricing metrics and return them as a dict.

    Returns
    -------
    dict with keys:
        timestamp, spot_price, realized_price, transferred_price,
        balanced_price, delta_price
    """
    client = GlassnodeClient(api_key=api_key)

    spot_data = client.get_current_price()
    realized_data = client.get_realized_price()
    balanced_data = client.get_balanced_price()
    delta_data = client.get_delta_price()

    ts_spot, spot_price = _latest_value(spot_data)
    _, realized_price = _latest_value(realized_data)
    _, balanced_price = _latest_value(balanced_data)
    _, delta_price = _latest_value(delta_data)

    # Transferred Price is derived: Realized Price − Balanced Price
    transferred_price = realized_price - balanced_price

    return {
        "timestamp": ts_spot.isoformat(),
        "spot_price": spot_price,
        "realized_price": realized_price,
        "transferred_price": transferred_price,
        "balanced_price": balanced_price,
        "delta_price": delta_price,
    }
