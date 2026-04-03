# get-onchain-data

A Python service that **periodically collects on-chain crypto metrics**, stores them in a local SQLite database, and exposes them through a [TradingView UDF](https://github.com/tradingview/charting_library/wiki/UDF)-compatible HTTP API as well as a generic REST API.

---

## Features

| Area | Detail |
|---|---|
| **Collection** | Bitcoin and Ethereum on-chain metrics from public APIs (no API key required by default) |
| **Scheduling** | Configurable collection interval via APScheduler (default: every hour) |
| **Storage** | Lightweight SQLite database with indexed time-series records |
| **Dashboard** | Real-time interactive web dashboard at `/` with live charts, matrix-style design, and full data history |
| **TradingView UDF** | `/udf/*` endpoints implement the UDF protocol so metrics can be plotted directly in TradingView |
| **REST API** | `/metrics/*` endpoints for generic consumption by other tools |
| **Google Sheets Sync** | Automatic sync of Realized and Balanced prices to Google Sheets via API with service account authentication |

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
| `BTC.MARKET_CAP` | Bitcoin market capitalization (USD) | Coin Metrics Community API |
| `BTC.MVRV` | Bitcoin MVRV ratio (market cap / realized cap) | Coin Metrics Community API |
| `BTC.CIRCULATING_SUPPLY` | Bitcoin circulating supply (BTC) | Coin Metrics Community API |
| `BTC.REALIZED_CAP` | Bitcoin realized capitalization (USD, derived) | Coin Metrics + derived |
| `BTC.AVERAGE_CAP` | Running average bitcoin market cap in local DB (USD, derived) | Local DB + derived |
| `BTC.REALIZED_PRICE` | Bitcoin Realized Price — avg on-chain cost basis (USD, derived) | Coin Metrics + derived |
| `BTC.DELTA_PRICE` | Bitcoin Delta Price — fundamental/technical floor model (USD, derived) | Coin Metrics + local average cap |
| `BTC.TRANSFERRED_PRICE_EST` | Bitcoin Transferred Price estimate (USD, proxy) | Heuristic estimate |
| `BTC.BALANCED_PRICE_EST` | Bitcoin Balanced Price estimate (USD, proxy) | Heuristic estimate |

> **Note:** Pricing metrics are computed from the free Coin Metrics Community API plus local historical averages in SQLite. No paid Glassnode subscription is required.

### How to read the on-chain pricing metrics

| Metric | What it means |
|---|---|
| **Realized Price** | The average on-chain cost basis of the market — Realized Cap divided by circulating supply. It tells you the average price at which every coin last moved on-chain. |
| **Transferred Price (EST)** | A proxy estimate derived from the modeled balanced-floor signal. It is clearly labeled as an estimate and not an exact Glassnode equivalent. |
| **Balanced Price (EST)** | A proxy fair-value floor based on the locally derived Delta Price signal. Clearly labeled as estimate. |
| **Delta Price** | (Realized Cap − Average Cap) / Circulating Supply, where Average Cap is the running average market cap stored locally. |

---

## Dashboard

The service includes a **real-time interactive web dashboard** at `http://localhost:8000` featuring:

- **Matrix-style design** — Dark green grid background with glowing accents
- **Live metric cards** — Priority-ordered cards with latest values and mini trend charts
- **Main combined BTC chart** — Large unified chart showing BTC Price, Realized Price, and Balanced Price with full historical data on a logarithmic Y-axis
- **Full-history charts** — All Realized and Balanced price data from the entire database (not limited by date range selector)
- **Interactive data table** — Per-metric historical data table with last 10 data points
- **Filters and search** — Filter metrics by symbol, adjust time range (7/30/90/365 days)
- **Live refresh** — Auto-updates every 30 seconds; manual refresh button available
- **Summary tiles** — Quick stats showing total metrics, BTC/ETH counts, and last refresh time

### Dashboard data flow

1. Browser loads `/` → Serves `static/index.html`
2. JavaScript fetches metric list from `/metrics`
3. For each metric, fetches current value from `/metrics/{symbol}`
4. Fetches history from `/metrics/{symbol}/history?from=...&to=...`
5. **Realized/Balanced prices always fetch `from=0`** (full history)
6. All data displayed with Chart.js line graphs

### Key dashboard URLs

| URL | Purpose |
|---|---|
| `/` | Main dashboard (HTML + JavaScript) |
| `/metrics` | JSON list of all metrics |
| `/metrics/{symbol}` | Latest value for a metric |
| `/metrics/{symbol}/history?from=X&to=Y` | Historical data in date range |

---

## Google Sheets Integration

Automatically sync Realized Price and Balanced Price data to a Google Sheet for external analysis, dashboards, or TradingView use.

### Setup (one-time)

1. **Create a Google Cloud project**
   - Go to [Google Cloud Console](https://console.cloud.google.com)
   - Create a new project

2. **Enable APIs**
   - Enable `Google Sheets API`
   - Enable `Google Drive API`

3. **Create a Service Account**
   - IAM & Admin → Service Accounts → Create Service Account
   - Give it a name (e.g., "onchain-data-sync")
   - Grant Editor role (for sheet access)

4. **Create and download JSON key**
   - In the Service Account, go to "Keys" tab
   - Create new key → JSON format
   - Download and save to your project directory (e.g., `credentials.json`)

5. **Create a Google Sheet**
   - Create a new sheet at [sheets.google.com](https://sheets.google.com)
   - Copy the Spreadsheet ID from the URL (e.g., `1ABC...XYZ`)

6. **Share the sheet with the service account**
   - Open `credentials.json` and copy the `client_email` field
   - Share your Google Sheet with that email address (Editor access)

7. **Configure environment variables** in `.env`:
   ```bash
   GOOGLE_SHEETS_CREDENTIALS_PATH=./credentials.json
   GOOGLE_SHEETS_SPREADSHEET_ID=1ABC...XYZ
   GOOGLE_SHEETS_SHEET_NAME=OnChainMetrics
   GOOGLE_SHEETS_ENABLE_BACKFILL_ON_STARTUP=true
   ```

### How it works

- **Incremental sync** — After each scheduled collection, the latest Realized/Balanced prices are appended to the sheet (daily rows with date, day, month, year, and prices)
- **Startup backfill** (optional) — On app startup, if `GOOGLE_SHEETS_ENABLE_BACKFILL_ON_STARTUP=true`, the entire historical database is pushed to Sheets (grouped by calendar date, one row per date)
- **Graceful no-op** — If credentials not configured, sheets sync silently skips without errors
- **Error resilience** — Failed sheet operations are logged and don't block metric collection

### Google Sheets output format

Each row has 6 columns:

| Column | Example | Purpose |
|---|---|---|
| `date` | 2024-01-15 | Calendar date for daily summary |
| `day` | 15 | Day of month (for TradingView/Sheets analysis) |
| `month` | 1 | Month of year (for seasonal analysis) |
| `year` | 2024 | Year (for multi-year analysis) |
| `realized_price` | 42000.50 | BTC Realized Price (USD) |
| `balanced_price_est` | 38250.75 | BTC Balanced Price estimate (USD) |

### Use case: TradingView

Once historical data is in Google Sheets:
1. Use Google Sheets as a data source in TradingView
2. Plot Realized and Balanced prices as a "fair value" layer
3. Combine with price action for on-chain confluence signals

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
| `COLLECT_INTERVAL_SECONDS` | `3600` | How often to fetch new metrics (seconds) |
| `DB_PATH` | `onchain_metrics.db` | SQLite database file path |
| `GOOGLE_SHEETS_CREDENTIALS_PATH` | *(unset)* | Path to service account JSON credentials file (e.g., `./credentials.json`) |
| `GOOGLE_SHEETS_SPREADSHEET_ID` | *(unset)* | Google Sheet ID from URL (e.g., `1ABC...XYZ`) |
| `GOOGLE_SHEETS_SHEET_NAME` | `OnChainMetrics` | Sheet tab name within the spreadsheet |
| `GOOGLE_SHEETS_ENABLE_BACKFILL_ON_STARTUP` | `false` | If `true`, backfill entire DB to sheet on app startup |

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
