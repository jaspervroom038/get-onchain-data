# get-onchain-data

A Python service that **periodically collects on-chain crypto metrics**, stores them in a local SQLite database, and exposes them through a [TradingView UDF](https://github.com/tradingview/charting_library/wiki/UDF)-compatible HTTP API as well as a generic REST API.

---

## Features

| Area | Detail |
|---|---|
| **Collection** | Bitcoin and Ethereum on-chain metrics from public APIs (no API key required by default) |
| **Scheduling** | Configurable collection interval via APScheduler (default: every hour) |
| **Storage** | Lightweight SQLite database with indexed time-series records |
| **TradingView UDF** | `/udf/*` endpoints implement the UDF protocol so metrics can be plotted directly in TradingView |
| **REST API** | `/metrics/*` endpoints for generic consumption by other tools |

### Metrics collected

| Symbol | Description | Source |
|---|---|---|
| `BTC.ACTIVE_ADDRESSES` | Unique active Bitcoin addresses | blockchain.info |
| `BTC.TRANSACTION_COUNT` | Confirmed Bitcoin transactions (24 h) | blockchain.info / Blockchair |
| `BTC.HASH_RATE` | Bitcoin network hash rate (TH/s) | blockchain.info / Blockchair |
| `BTC.DIFFICULTY` | Bitcoin mining difficulty | blockchain.info / Blockchair |
| `BTC.FEE_MEDIAN` | Median Bitcoin transaction fee (satoshis) | Blockchair |
| `ETH.GAS_PRICE` | Average Ethereum gas price (Gwei) | Blockchair |
| `ETH.TRANSACTION_COUNT` | Confirmed Ethereum transactions (24 h) | Blockchair |
| `BTC.REALIZED_PRICE` | Bitcoin Realized Price — avg on-chain cost basis (USD) | Glassnode |
| `BTC.TRANSFERRED_PRICE` | Bitcoin Transferred Price — time/volume-weighted spending (USD) | Glassnode (derived) |
| `BTC.BALANCED_PRICE` | Bitcoin Balanced Price — fair value indicator (USD) | Glassnode |
| `BTC.DELTA_PRICE` | Bitcoin Delta Price — fundamental/technical floor model (USD) | Glassnode |

> **Note:** The four pricing metrics (`BTC.REALIZED_PRICE`, `BTC.TRANSFERRED_PRICE`, `BTC.BALANCED_PRICE`, `BTC.DELTA_PRICE`) require a `GLASSNODE_API_KEY`. Without a key, these metrics are simply skipped and the remaining collectors continue to work normally.

### How to read the on-chain pricing metrics

| Metric | What it means |
|---|---|
| **Realized Price** | The average on-chain cost basis of the market — Realized Cap divided by circulating supply. It tells you the average price at which every coin last moved on-chain. |
| **Transferred Price** | A time- and volume-weighted measure of historical spending activity. It corrects for how many "old" coins were spent at much lower prices. |
| **Balanced Price** | Realized Price − Transferred Price. Glassnode considers this roughly a "fair value" floor that tends to be reached at the end of bear markets. |
| **Delta Price** | (Realized Cap − Average Cap) / Circulating Supply. A hybrid fundamental/technical bottom model that has historically marked final cycle lows. |

---

## Quick start

### 1. Clone and install

```bash
git clone https://github.com/jaspervroom038/get-onchain-data.git
cd get-onchain-data
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure (optional)

Copy `.env.example` to `.env` and adjust settings:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `HOST` | `0.0.0.0` | HTTP server bind address |
| `PORT` | `8000` | HTTP server port |
| `COLLECT_INTERVAL_SECONDS` | `3600` | How often to fetch new metrics |
| `DB_PATH` | `onchain_metrics.db` | SQLite database file path |
| `GLASSNODE_API_KEY` | *(empty)* | Glassnode API key for pricing metrics (optional) |

### 3. Run

```bash
python main.py
```

The server starts, immediately performs a first collection, then collects on the configured schedule.  
The API is available at `http://localhost:8000`.

---

## API reference

### TradingView UDF endpoints

| Endpoint | Description |
|---|---|
| `GET /udf/config` | Server capabilities |
| `GET /udf/time` | Current server unix timestamp |
| `GET /udf/search?query=BTC` | Symbol search |
| `GET /udf/symbols?symbol=BTC.HASH_RATE` | Symbol metadata |
| `GET /udf/history?symbol=BTC.HASH_RATE&resolution=1D&from=<ts>&to=<ts>` | Historical bars |

### Generic REST endpoints

| Endpoint | Description |
|---|---|
| `GET /metrics` | List all stored symbols |
| `GET /metrics/{symbol}` | Latest value for a symbol |
| `GET /metrics/{symbol}/history?from=<ts>&to=<ts>` | Time-range history |

Interactive API docs are available at `http://localhost:8000/docs`.

---

## TradingView integration

1. In TradingView's charting library, set `datafeed` to a UDF datafeed pointing at `http://<host>:8000/udf`.
2. Search for any of the supported symbols (e.g., `BTC.HASH_RATE`).
3. The metric will appear as a line chart using daily or hourly bars.

---

## Development

### Run tests

```bash
python -m pytest tests/ -v
```

### Project structure

```
get-onchain-data/
├── main.py          # Entry point (scheduler + HTTP server)
├── config.py        # Settings (from environment / .env)
├── collector.py     # On-chain metric collectors
├── scheduler.py     # APScheduler periodic collection
├── server.py        # FastAPI HTTP server (UDF + REST)
├── storage.py       # SQLite persistence layer
├── requirements.txt
├── .env.example
└── tests/
    ├── test_collector.py
    ├── test_server.py
    └── test_storage.py
```
