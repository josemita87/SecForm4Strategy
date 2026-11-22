# Configuration

Each service reads two kinds of configuration:

- **Environment** (`config.py`, pydantic-settings): credentials, hosts, run modes and tunables. Required fields have no default, so a missing variable fails at boot.
- **YAML spec** (one `spec.yaml` next to each service's code): static data that changes behaviour without a code change.

## Backends: local or AWS

The same code runs locally and on AWS. Environment variables choose each backend, and `main.py` builds the matching adapter.

| Setting | Services | Local | AWS |
| --- | --- | --- | --- |
| `QUEUE__BACKEND` | master-index, scraper, kafka-to-store | `kafka`: `QUEUE__BROKER_ADDRESS`; consumers also need `QUEUE__CONSUMER_GROUP` and `QUEUE__AUTO_OFFSET_RESET` | `sqs`: `QUEUE__REGION` |
| `MODEL_STORE__BACKEND` | training, inference | `local` (default): `MODEL_STORE__PATH`, default `MODEL_PATH` in the service's `paths.py` | `s3`: `MODEL_STORE__REGION`, `MODEL_STORE__BUCKET`, `MODEL_STORE__PREFIX` (default `models`) |
| `PYICEBERG_CATALOG__DEFAULT__*` | every feature-store service | `TYPE=sql`, a SQLite catalog | `TYPE=glue` |

`INPUT_QUEUE` and `OUTPUT_QUEUE` name the queues: Kafka topics locally, SQS queues on AWS. A field that the chosen backend requires and that is not set fails validation at startup.

## `feature_groups`: feature-group catalogs

Each service lists the feature groups it touches, keyed by the reference the code uses (a `FeatureGroup` enum value).

| Field | Required | Meaning |
| --- | --- | --- |
| `primary_key` | yes | Upsert key columns |
| `event_time` | yes | Event-time column |
| `name` | no | Feature-group name; defaults to the reference key |

`version` is not in the file: `load_feature_group_catalog` injects it from `FEATURE_GROUP_VERSION`, so one template serves every version. Entries are validated against `FeatureGroupSpec` and unknown keys are rejected; `make validate-config` checks every catalog. The version is 1. A table's schema is fixed when the table is created, so a change to a table's key or columns means rebuilding the tables (drop them, or start a fresh lakehouse). To keep the stored data instead, bump the version.

The transaction `key` is deterministic: a hash of the filing's accession, its table (derivative or not) and the transaction's position in that table. A filed accession never changes, so every redelivery (feed re-polls, Kafka retries, backfill reruns) upserts the same row instead of adding a duplicate. 

Current formats:

- **BT4/BI4** (one row per transaction): the scraper's record, including `accession` and `position` (the transaction's place in its filing's table), `accepted_at` as a UTC timestamp, and share and holding counts as `float64`.
- **P** (one row per ticker and day, key `<ticker>@<date>`): split- and dividend-adjusted `open`/`close` for returns, `close_as_traded` (the close in that day's shares, undoing later splits) for valuation, and the day's `split` and `dividend`.
- **RT4/BIR4** (one row per ticker and next market print, key `<ticker>@<next print>`): every filing disclosed before that tradable price, valued at the as-traded close of the last trading day before the acceptance day, with `reported_price`, the mean price reported on its stock transactions (missing if none), `price_to_close`, USD values of the traded shares, the positions left and `total_held` (traded plus untraded accounts), the cluster features below, and for RT4 the label and `label_known_at`. A row's `avg_target_expanding` averages its ticker's labels already known at its acceptance; RT4 keeps every labelled row, the average missing until a label is known.

| Service | Groups | Role |
| --- | --- | --- |
| kafka-to-store | `bt4`, `bi4` | writes the raw training / inference transactions |
| yahoo_connect | `bt4`, `bi4`, `p` | reads tickers, writes prices |
| target | `p`, `bt4`, `bi4`, `rt4`, `bir4` | joins transactions with prices, writes labelled `rt4` and scored-input `bir4` |
| training | `rt4` | reads the labelled transactions |
| inference | `bir4` | reads the transactions to score |
| site-publisher | `rt4`, `bt4`, `bi4` | reads the labelled transactions for `model.json`, and the recent sales for `explore.json` |

## Other `spec.yaml` sections

**training**

- `columns_to_drop`: removed before the model sees the frame.
- `identification_features`: kept for traceability, excluded from the model inputs. `accepted_at` (acceptance) and `label_known_at` (exit close), both UTC timestamps, drive the holdout split and the walk-forward folds: the holdout is the latest `HOLDOUT_RATIO` of filings, and each training set keeps only labels known before its validation or holdout period starts ([training.md](training.md)).
- `short_return` (-0.05): the label. A row is a short winner when its `pct_change` is below this value.
- `model_params`: fixed keyword arguments for `HistGradientBoostingClassifier` (`random_state` comes from `RANDOM_STATE`).
- `param_grid`: the values the grid search tries on top of `model_params`.
- `cutoff_grid`: the probability cutoffs that training scores on the development folds. Training also reports the holdout shorts at each of them.
- `min_fold_shorts` (100): a cutoff qualifies only when it shorts at least this many rows in every fold.
- `fallback_cutoff` (0.5): the cutoff that training saves when no cutoff qualifies.

Inference has only its catalog: the model's input columns, their order and the probability cutoff come from the trained model's metadata (`features`, `probability_cutoff`). The optional `PROBABILITY_CUTOFF` variable replaces the saved cutoff.

**target**

- `cluster_window_days` (30): how far back each disclosure row counts its ticker's distinct insiders, officers and filings (`insiders_recent`, `officers_recent`, `filings_recent`), ending at the row's acceptance.
- `max_label_return` (3.0): the largest plausible label. `compute_target` leaves `pct_change` missing above it, and also when the entry or exit adjusted price is not positive. Training drops such rows as unlabelled.

Target collapses transactions into one row per ticker and *next market print* (the first 09:30 open or 16:00 close after acceptance): everything disclosed before a tradable price, with stock and derivative quantities apart, code and role flags, filing and insider counts and the filing lag (`aggregate_disclosures` in target `main.py`). One row per trading opportunity keeps every filing timed at its own window and gives each label once.

**site-publisher**

`Spec` in `site-publisher/src/config.py` validates these keys:

- `live_functions`: the live Lambdas that `health.json` reports, each named `<name_prefix>-live-<function>`.
- `live_runs` (50, at most 1000): the newest `live-score` runs that `live.json` lists.
- `importance`: permutation importance on a sample of the holdout. `n_repeats` (3) is the number of shuffles per feature, `random_state` (42) the seed, `max_rows` (20000) the sample size, and `top` (15) the number of features kept.
- `explore`: the recent insider sales of `explore.json`. `days` (7) is the window, `limit` (25) the length of each list, and `every_minutes` (15) the rebuild interval. `every_minutes` must divide 60.
- `orders_limit` (100, at most 500): the Alpaca orders that `portfolio.json` lists.
- `equity_period` (`1M`) and `equity_timeframe` (`1D`): the period and the resolution of the Alpaca equity history.

## Reference data (target)

`prices-feature-pipeline/target/src/data/` holds static company data joined onto every disclosure row (`add_company_context`):

- `mcaps.parquet`: monthly market-cap snapshots (`ticker`, `marketcap`, `fbd` = first business day), 1992 to 2024-08. Each row takes the latest snapshot strictly before its acceptance day.
- `companies.json`: `{field: {cik: value}}` for `ticker`, `sic`, `sic_group` and `exchange`, keyed by zero-padded issuer CIK.

Paths default to these files and can be overridden with `MARKET_CAPS_PATH` and `COMPANIES_PATH`.

## Model artifacts

Training saves the fitted pipeline as `model.joblib` and its metadata beside it as `model.meta.json`: training time, git SHA (`GIT_SHA`, else `git rev-parse HEAD`), scikit-learn version, feature-group version, target, ordered `features`, model parameters, row counts, holdout start, the `Scores` of the model and its baselines on the holdout, the probability cutoff, the label return, the holdout base rate, the per-fold validation average precision and base rate, and the holdout shorts at each cutoff of `cutoff_grid`, as the `ModelMetadata` record (`secform4strategy_clients.records`). The pipeline addresses columns by position, so inputs must follow `features`. Inference refuses a model saved under another scikit-learn version, and a model without `probability_cutoff`. Each save writes a new version folder named after the training time. Inference loads only the promoted version, and `PROMOTE` decides whether training promotes its model ([training.md](training.md#promotion)).
