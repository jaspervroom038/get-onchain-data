#!/usr/bin/env python3
"""Entry-point: fetch and display on-chain Bitcoin pricing metrics."""

from __future__ import annotations

import json
import sys

from tabulate import tabulate

from src.metrics import fetch_metrics


def _fmt_usd(value: float) -> str:
    """Format a USD value with thousands separator and 2 decimals."""
    return f"${value:,.2f}"


def display_metrics(metrics: dict, output_json: bool = False) -> None:
    """Pretty-print the metrics to stdout."""
    if output_json:
        print(json.dumps(metrics, indent=2))
        return

    rows = [
        ["Spot Price (BTC/USD)", _fmt_usd(metrics["spot_price"])],
        ["Realized Price", _fmt_usd(metrics["realized_price"])],
        [
            "Transferred Price",
            _fmt_usd(metrics["transferred_price"]),
        ],
        ["Balanced Price", _fmt_usd(metrics["balanced_price"])],
        ["Delta Price", _fmt_usd(metrics["delta_price"])],
    ]

    print(f"\n  Bitcoin On-Chain Pricing Metrics  ({metrics['timestamp']})")
    print("=" * 60)
    print(tabulate(rows, headers=["Metric", "Value"], tablefmt="simple"))

    print("\n  Definitions")
    print("-" * 60)
    print(
        "  Realized Price      = average on-chain cost basis\n"
        "  Transferred Price   = time & volume-weighted historical\n"
        "                        spending activity measure\n"
        "  Balanced Price      = Realized Price - Transferred Price\n"
        '                        ("fair value" at bear-market bottoms)\n'
        "  Delta Price         = (Realized Cap - Avg Cap) / Supply\n"
        "                        (fundamental/technical bottom model)\n"
    )


def main() -> None:
    """Run the metrics fetcher."""
    output_json = "--json" in sys.argv
    try:
        metrics = fetch_metrics()
        display_metrics(metrics, output_json=output_json)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
