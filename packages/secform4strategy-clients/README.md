# secform4strategy-clients

Pure infrastructure clients shared across the SecForm4Strategy microservices.

Each client is a thin, parameterised wrapper around an external SDK. The clients
hold **no domain knowledge** — no hardcoded feature-group names or keys, no
field parsing, no data cleaning, and they never import a service `config`.
Callers pass everything in explicitly and own all domain logic.

## Clients and extras

Every SDK lives behind an optional dependency ("extra") and is imported at the top
of its one client module; the package `__init__` imports nothing, so a service only
needs the extras of the client modules it imports:

One client per external service (e.g. a single `KafkaClient` covers both
consuming and producing — not separate consumer/producer classes):

| Client | Module | Extra | SDK |
| --- | --- | --- | --- |
| `IcebergFeatureStore` | `secform4strategy_clients.feature_store` (catalog: `feature_groups`) | `feature-store` | `pyiceberg` |
| `AlpacaClient` (orders and account reads) | `secform4strategy_clients.broker` | `broker` | `alpaca-py` |
| `KafkaClient` | `secform4strategy_clients.messaging` | `messaging` | `quixstreams` |
| `SqsQueue` (messages and queue depths) | `secform4strategy_clients.sqs` | `aws` | `boto3` |
| `DynamoSeenKeys` | `secform4strategy_clients.dynamodb` | `aws` | `boto3` |
| `SsmSettings` (settings base reading SSM parameters on Lambda) | `secform4strategy_clients.ssm_settings` | `aws` | `boto3`, `pydantic-settings` |
| `EdgarClient` | `secform4strategy_clients.edgar` | `edgar` | `httpx` |
| `MarketDataClient` | `secform4strategy_clients.market_data` | `market-data` | `yfinance` |
| `LocalModelStore` | `secform4strategy_clients.model_store` | `model-store` | `joblib` |
| `S3ModelStore` | `secform4strategy_clients.s3_model_store` | `model-store`, `aws` | `joblib`, `boto3` |
| `StepFunctions` | `secform4strategy_clients.stepfunctions` | `stepfunctions` | `boto3` |
| `Scheduler` | `secform4strategy_clients.scheduler` | `scheduler` | `boto3` |
| `CloudWatchMetrics` | `secform4strategy_clients.cloudwatch` | `cloudwatch` | `boto3` |
| `S3Site` (a static site's JSON data files) | `secform4strategy_clients.s3_site` | `s3-site` | `boto3` |

The site publisher is the only service that uses `StepFunctions`, `Scheduler`, `CloudWatchMetrics`, `S3Site` and the read methods of `AlpacaClient`, `SqsQueue`, `S3ModelStore` and `IcebergFeatureStore`.

## Ports

`secform4strategy_clients.ports` defines the Protocols services depend on:
`FeatureStore`, `ModelStore`, `Broker`, `MarketData`, `MessageQueue`, `SeenKeys` and `Filings`.
The site publisher's read-only ports are `TradingAccount`, `RecentRows`, `ModelFiles`, `Site`,
`Executions`, `Schedules`, `Queues` and `Metrics`. The module also defines the order literals
`Side`, `OrderType` and `TimeInForce`, the `Filters` read filter, and the errors
`RetryableError` and `PermanentError`.

`secform4strategy_clients.records` defines the records the ports exchange: the named tuples
`Filing`, `Message`, `Execution`, `ExecutionSummary`, `HistoryEvent`, `QueueDepth`,
`FunctionMetrics`, `BrokerAccount`, `BrokerPosition`, `BrokerOrder` and `EquityPoint`, the
frozen dataclasses `Scores` and `ModelMetadata`, and the `RunStatus` literal. Services
annotate their dependencies with a port, never a concrete client, and each
service's `main.py` (or `handler.py` on Lambda) is the one place that builds the
clients from config and passes them in. Swapping a backend (a local catalog for AWS Glue, local disk for a bucket)
is a new adapter that satisfies the port, with no service change.

`ModelStore.save` writes a model and its metadata as a new version and returns the version.
It does not change which model `load` returns. `ModelStore.promote(version)` makes that version
the one `load` returns: `S3ModelStore` writes `latest.json`, and `LocalModelStore` copies the
version's files to its path. `ModelStore` includes the `ModelFiles` methods `latest_version` and
`metadata`.

`FeatureStore.read` filters with a plain mapping, `{column: value}`, where an
iterable value means "any of". Each adapter translates it to its own query.

Adapters retry transient failures with `retry.with_backoff`, then raise `RetryableError` or
`PermanentError` from the SDK's exception. AWS adapters leave retries to botocore and share
`aws.classified` for the mapping; `docs/error-handling.md` gives the contract and how
services act on each.

`secform4strategy_clients.fakes` holds typed in-memory implementations of the
ports for tests, one module per port (`fakes.filings`, `fakes.market_data`, `fakes.messaging`, `fakes.models`, `fakes.seen`), each
bound to its Protocol so `make type-check` fails when one drifts.

```toml
# in a service pyproject.toml
dependencies = ["secform4strategy-clients[feature-store,messaging]"]

[tool.uv.sources]
secform4strategy-clients = { path = "../../packages/secform4strategy-clients", editable = true }  # depth varies per service
```

## Usage

```python
from pathlib import Path

from pyiceberg.catalog import load_catalog
from secform4strategy_clients.feature_groups import load_feature_group_catalog
from secform4strategy_clients.feature_store import IcebergFeatureStore

fs = IcebergFeatureStore(
    load_catalog('default'), 'secform4strategy', load_feature_group_catalog(Path('spec.yaml'), version=1)
)
bt4 = fs.read('bt4', where={'acquired_disposed': 'D', 'ticker': ['AAPL', 'MSFT']})
fs.push('rt4', labelled)
```

`load_feature_group_catalog` validates every entry of the YAML catalog into a
frozen `FeatureGroupSpec` (`primary_key`, `event_time`, optional `name`, plus the
injected `version`) and rejects unknown keys; `make validate-config` runs it over every service's catalog.
