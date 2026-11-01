# SecForm4Strategy

**A microservice ML system that turns SEC Form 4 insider-trading filings into a short-side equity signal.**

When a corporate insider sells their own company's stock, they must disclose it to the SEC on a Form 4 within two business days. SecForm4Strategy ingests those filings in near-real-time, joins each transaction to the stock's forward price action, trains a model to predict the return that *follows* an insider sale, and shorts the names it expects to fall, paper-traded through Alpaca.

> **Project status: portfolio / research.** This is an end-to-end systems-and-ML engineering showcase, not a production trading system or investment advice. It runs against paper-trading and a personal Hopsworks project, and the strategy is illustrative.

---

## Why it's interesting

**The motivating question:** do corporate insiders actually beat the market? Insiders trade their own company's stock with an information advantage no one else has, and I have long wanted to settle empirically whether their sales precede real underperformance or whether the signal is already priced in. SecForm4Strategy is the apparatus built to answer that question end to end, and the engineering below is what makes the answer trustworthy.

- **A real, messy data pipeline:** SEC EDGAR scraping, XML parsing, a streaming bus, a feature store, and a market-data feed, wired into one coherent system.
- **Disclosure time, not trade time:** every transaction is stamped with the moment SEC accepted its filing. The label enters at the first price a trader could act on after that moment: the same day's close for filings accepted in session, the next open otherwise. Not at the insider's trade date, which can be days earlier.
- **A leakage-aware target:** the per-ticker expanding average only uses returns already realised (their exit close printed) when a filing became public.
- **Clean service boundaries:** each microservice owns one stage, and all external SDKs sit behind a single shared `secform4strategy-clients` package that holds *zero* domain knowledge.
- **Reproducible, typed, linted:** a `uv` monorepo with per-service lockfiles and a repo-wide quality gate (ruff, mypy, pydoclint, bandit).

---

## Architecture

Two pipelines feed one feature store; two "systems" (training and inference) run the same services in different modes.

```
  SEC EDGAR                          Yahoo Finance
      │                                    │
      ▼                                    ▼
 master-index ──URLs──▶ scraper      yahoo-connect
                          │ Form 4 txns      │ daily closes
                          ▼                  │
                       Redpanda              │
                       (Kafka)               │
                          │                  │
                          ▼                  ▼
                    kafka-to-store        ┌────────┐   Hopsworks Feature Store
                          │               │        │   ┌───────────────────────┐
                          └──────────────▶│        ├──▶│ P    prices            │
                            BT4 / BI4      └────────┘   │ BT4  train txns        │
                                                        │ BI4  inference txns    │
                                  target ──────────────▶│ RT4  train + target    │
                          (join txns × prices,          │ BIR4 inference + target│
                           forward return + expanding   └───────────────────────┘
                           per-ticker average)                │            │
                                                     RT4 ◀─────┘            └─────▶ BIR4
                                                      │                            │
                                                      ▼                            ▼
                                      training (scikit-learn regressor)    inference (score model)
                                                      │                            │
                                                 saved model ──────────────────────┤
                                                                                   ▼
                                                                          Alpaca (short sells)
```

### The data contract

Every feature group shares one contract (`primary_key=['key']`, `event_time='date'`), so services join cleanly without bespoke glue. The catalog (the `feature_groups` section of each service's `spec.yaml`):

| Group | Meaning | Produced by | Consumed by |
| --- | --- | --- | --- |
| `P` | Daily open and close prices | yahoo-connect | target |
| `BT4` | Basic **T**raining transactions (Form 4) | kafka-to-store | target, yahoo-connect |
| `RT4` | **R**eturn training transactions (BT4 + forward-return target) | target | training |
| `BI4` | Basic **I**nference transactions (Form 4) | kafka-to-store | target, yahoo-connect |
| `BIR4` | **R**eturn inference transactions (BI4 + mapped target) | target | inference |

Kafka topics mirror the split: `tmi`/`imi` (training/inference master index) feed `td`/`id` (training/inference transaction dictionaries).

---

## Services

| Service | Role |
| --- | --- |
| **sec-feature-pipeline/master-index** | Discovers Form 4 filings, from the quarterly master index (backfill) or by polling EDGAR's current-filings Atom feed (live), and publishes them to Kafka. |
| **sec-feature-pipeline/scraper** | Fetches each filing, parses the embedded Form 4 XML into transaction records stamped with the filing's acceptance time, publishes them. |
| **sec-feature-pipeline/kafka-to-store** | Consumes transactions, validates them, and upserts into the `BT4`/`BI4` feature groups. |
| **prices-feature-pipeline/yahoo-connect** | Resolves the tickers referenced by transactions and ingests their daily open/close history into `P`. |
| **prices-feature-pipeline/target** | Joins transactions to prices, computes the forward return from the first open or close after acceptance over `DELTA_PERIOD` days, derives the leakage-free expanding per-ticker average, and writes `RT4` (train) / `BIR4` (inference). |
| **training** | Loads `RT4`, trains a scikit-learn pipeline (preprocessing + gradient-boosted regressor) on the forward-return target, runs a short-side back-test, and saves the fitted pipeline. |
| **inference** | Loads the saved model, scores the latest `BIR4`, and places short-sell orders via Alpaca for names predicted to fall. |
| **packages/secform4strategy-clients** | Shared, domain-free wrappers for every external SDK (Hopsworks, Alpaca, Kafka/quixstreams, EDGAR, yfinance). See its [README](packages/secform4strategy-clients/README.md). |

### System modes

The same services run in two configurations, selected by env files and Compose profiles:

- **Training system:** builds the labelled dataset (`RT4`) and trains the model.
- **Inference system:** builds the live dataset (`BIR4`) and produces predictions/orders.

Within each, two profiles let you bring the halves up independently:

- `sec`: master-index, scraper, kafka-to-store (transactions)
- `prices`: yahoo-connect, target (prices + target computation)

---

## Repository layout

```
.
├── sec-feature-pipeline/        # Form 4 ingestion (master-index, scraper, kafka-to-store)
├── prices-feature-pipeline/     # price + target features (yahoo_connect, target)
├── training/                    # scikit-learn training + short-side simulation
├── inference/                   # model scoring + Alpaca order placement
├── packages/secform4strategy-clients/ # shared, domain-free SDK wrappers (path-dependency)
├── docker-compose/              # redpanda + system-{training,inference} stacks and env files
├── Makefile                     # quality gate + compose entrypoints
└── pyproject.toml               # repo-wide ruff/mypy/pydoclint/bandit config
```

Each service is its own `uv` project (own `pyproject.toml` + `uv.lock`) and Dockerfile, building from the repo root so it can pull in the shared `secform4strategy-clients` package by path.

---

## Tech stack

- **Python 3.14** everywhere, managed with [`uv`](https://docs.astral.sh/uv/) (per-service lockfiles, `uv sync --frozen` in Docker).
- **Streaming:** Redpanda (Kafka API) via `quixstreams`.
- **Feature store:** Hopsworks.
- **ML:** scikit-learn `HistGradientBoostingRegressor` in a `Pipeline` with its preprocessing (regression on forward returns).
- **Market data / filings:** `yfinance`, SEC EDGAR.
- **Broker:** Alpaca (paper trading).
- **Config:** `pydantic-settings` for env/secret knobs, plus YAML spec templates for static specs (feature-group catalogs, cluster window, feature-selection lists).
- **Quality gate:** ruff (lint + format), mypy, pydoclint, and bandit, all driven by one shared config in `pyproject.toml`.

---

## Running it locally

### Prerequisites

- Docker + Docker Compose
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- A contact for SEC's fair-access policy, and (for the inference system) Alpaca paper-trading credentials

### Secrets

Secrets are **never** committed, and services hold them as `SecretStr`, unwrapped only where an SDK needs them. The SEC pipeline reads `SEC_USER_AGENT` (a real name and contact email, e.g. `export SEC_USER_AGENT="Jane Doe jane@example.com"`) from the host environment; Compose refuses to start without it. Each stack reads a git-ignored `.credentials.env` (next to the Compose file) containing, as needed:

```dotenv
ALPACA_API_KEY=...
ALPACA_API_SECRET=...
ALPACA_BASE_URL=https://paper-api.alpaca.markets
```

### Bring up the pipeline

```bash
# 1. Start the Redpanda (Kafka) cluster
make start-redpanda

# 2a. Training system: populate transactions, then prices + target
make training-sec
make training-prices

# 2b. or Inference system
make inference-sec
make inference-prices
```

Then run the standalone `training` or `inference` service against the populated feature store.

### Code quality

```bash
make quality       # format + lint + docstrings
make type-check    # mypy
make security      # bandit (high severity)
```

---

## License

Released under the MIT License.
