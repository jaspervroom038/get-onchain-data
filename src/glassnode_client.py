"""Glassnode API client for fetching on-chain Bitcoin metrics."""

from typing import Any

import requests

from src.config import GLASSNODE_API_KEY, GLASSNODE_BASE_URL, ASSET


class GlassnodeClient:
    """Thin wrapper around the Glassnode REST API."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or GLASSNODE_API_KEY
        if not self.api_key:
            raise ValueError(
                "Glassnode API key is required. "
                "Set GLASSNODE_API_KEY in your .env file or pass it explicitly."
            )
        self.base_url = GLASSNODE_BASE_URL

    def _get(self, endpoint: str, params: dict[str, Any] | None = None) -> list[dict]:
        """Perform a GET request and return the JSON response."""
        params = params or {}
        params.setdefault("a", ASSET)
        params["api_key"] = self.api_key

        url = f"{self.base_url}{endpoint}"
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def get_realized_price(self) -> list[dict]:
        """Fetch Realized Price (USD).

        Realized Price = Realized Cap / Circulating Supply.
        It represents the average on-chain cost basis of the market.
        """
        return self._get("/v1/metrics/market/price_realized_usd")

    def get_balanced_price(self) -> list[dict]:
        """Fetch Balanced Price (USD).

        Balanced Price = Realized Price − Transferred Price.
        Glassnode considers this a "fair value" indicator useful at bear-market bottoms.
        """
        return self._get("/v1/metrics/indicators/balanced_price_usd")

    def get_delta_price(self) -> list[dict]:
        """Fetch Delta Price (USD).

        Delta Price = (Realized Cap − Average Cap) / Circulating Supply.
        A hybrid fundamental/technical bottom model.
        """
        return self._get("/v1/metrics/indicators/delta_price_usd")

    def get_realized_cap(self) -> list[dict]:
        """Fetch Realized Cap (USD)."""
        return self._get("/v1/metrics/market/marketcap_realized_usd")

    def get_market_cap(self) -> list[dict]:
        """Fetch Market Cap (USD)."""
        return self._get("/v1/metrics/market/marketcap_usd")

    def get_current_price(self) -> list[dict]:
        """Fetch current BTC spot price (USD)."""
        return self._get("/v1/metrics/market/price_usd_close")
