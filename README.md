# get-onchain-data

Fetch and display key on-chain Bitcoin pricing metrics from the
[Glassnode](https://glassnode.com) API.

## Metrics

| Metric | Description |
|---|---|
| **Realized Price** | The average on-chain cost basis of the market (`Realized Cap / Circulating Supply`). |
| **Transferred Price** | A time- and volume-weighted measure of historical spending activity. Adjusts for "old" coins that were spent at much lower prices. |
| **Balanced Price** | `Realized Price − Transferred Price`. Glassnode considers this a "fair value" indicator at the end of bear markets. |
| **Delta Price** | `(Realized Cap − Average Cap) / Circulating Supply`. A hybrid fundamental / technical bottom model. |

## Prerequisites

* Python 3.10+
* A Glassnode API key (free tier works for some metrics; higher tiers unlock
  Balanced Price and Delta Price). Register at
  <https://studio.glassnode.com/settings/api>.

## Setup

```bash
# 1. Clone the repo
git clone https://github.com/jaspervroom038/get-onchain-data.git
cd get-onchain-data

# 2. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate   # Linux / macOS
# venv\Scripts\activate    # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure your API key
cp .env.example .env
# Edit .env and replace `your_api_key_here` with your actual key
```

## Usage

```bash
# Pretty table output
python main.py

# JSON output (machine-readable)
python main.py --json
```

### Example output

```
  Bitcoin On-Chain Pricing Metrics  (2026-04-02T00:00:00+00:00)
============================================================
Metric                  Value
----------------------  -----------
Spot Price (BTC/USD)    $84,123.45
Realized Price          $43,210.00
Transferred Price       $21,605.00
Balanced Price          $21,605.00
Delta Price             $14,567.00
```

## Project structure

```
get-onchain-data/
├── main.py               # Entry point
├── src/
│   ├── __init__.py
│   ├── config.py         # Environment / configuration
│   ├── glassnode_client.py  # Glassnode API wrapper
│   └── metrics.py        # Metric fetching & calculation logic
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```