# Configuration

Each service reads two kinds of configuration:

- **Environment** (`config.py`, pydantic-settings): credentials, hosts, run modes and tunables. Required fields have no default, so a missing variable fails at boot.
- **YAML spec** (one `spec.yaml` next to each service's code): static data that changes behaviour without a code change.

## `feature_groups`: feature-group catalogs

Each service lists the feature groups it touches, keyed by the reference the code uses (a `FeatureGroup` enum value).

| Field | Required | Meaning |
| --- | --- | --- |
| `primary_key` | yes | Upsert key columns |
| `event_time` | yes | Event-time column |
| `name` | no | Feature-group name; defaults to the reference key |

`version` is not in the file: `load_feature_group_catalog` injects it from `FEATURE_GROUP_VERSION`, so one template serves every version. Entries are validated against `FeatureGroupSpec` and unknown keys are rejected; `make validate-config` checks every catalog. A change to a table's key or column types needs a new version, since a table's schema is fixed when it is created.

The transaction `key` is deterministic: a hash of the filing's accession, its table (derivative or not) and the transaction's position in that table. A filed accession never changes, so every redelivery (feed re-polls, Kafka retries, backfill reruns) upserts the same row instead of adding a duplicate. Version 2 introduced this key; version 1 keyed on accession, remaining shares and the derivative flag, which collided.

| Service | Groups | Role |
| --- | --- | --- |
| kafka-to-store | `bt4`, `bi4` | writes the raw training / inference transactions |
| yahoo_connect | `bt4`, `bi4`, `p` | reads tickers, writes prices |
| target | `p`, `bt4`, `bi4`, `rt4`, `bir4` | joins transactions with prices, writes labelled `rt4` and scored-input `bir4` |
| training | `rt4` | reads the labelled transactions |
| inference | `bir4` | reads the transactions to score |

## Other `spec.yaml` sections

**training**

- `columns_to_drop`: removed before the model sees the frame.
- `identification_features`: kept for traceability, excluded from the model inputs. `timestamp` (acceptance) and `label_known_at` (exit close) drive the chronological, purged train/test split: the test set is the latest `TRAIN_TEST_RATIO` of filings, and training keeps only labels known before it starts.
- `model_params`: keyword arguments for `HistGradientBoostingRegressor` (`random_state` comes from `RANDOM_STATE`).

Inference has only its catalog: the model's input columns and their order come from the trained model's metadata (`features`).

**target**

- `agg_dict`: per-column aggregation that collapses same-ticker, same-day transactions into one daily row (`computations.data_aggregation`). Values are pandas aggregation names, except the `mode_first` token, which resolves to the most frequent value (first on ties).

## Reference data (target)

`prices-feature-pipeline/target/src/data/` holds static company data joined onto every daily transaction row (`add_company_context`):

- `mcaps.parquet`: monthly market-cap snapshots (`ticker`, `marketcap`, `fbd` = first business day), 1992 to 2024-08. Each row takes the latest snapshot strictly before its acceptance day.
- `companies.json`: `{field: {cik: value}}` for `ticker`, `sic`, `sic_group` and `exchange`, keyed by zero-padded issuer CIK.

Paths default to these files and can be overridden with `MARKET_CAPS_PATH` and `COMPANIES_PATH`.

## Model artifacts

Training saves the fitted pipeline as `model.joblib` and its metadata beside it as `model.meta.json`: training time, git SHA (`GIT_SHA`, else `git rev-parse HEAD`), scikit-learn version, feature-group version, target, ordered `features`, model parameters, row counts, test start and the `Scores` of the model and its baselines, as the `ModelMetadata` record (`secform4strategy_clients.ports`). The pipeline addresses columns by position, so inputs must follow `features`. Inference refuses a model saved under another scikit-learn version.
