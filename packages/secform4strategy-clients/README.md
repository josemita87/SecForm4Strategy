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
| `AlpacaClient` | `secform4strategy_clients.broker` | `broker` | `alpaca-py` |
| `KafkaClient` | `secform4strategy_clients.messaging` | `messaging` | `quixstreams` |
| `EdgarClient` | `secform4strategy_clients.edgar` | `edgar` | `httpx` |
| `MarketDataClient` | `secform4strategy_clients.market_data` | `market-data` | `yfinance` |
| `LocalModelStore` | `secform4strategy_clients.model_store` | `model-store` | `joblib` |

## Ports

`secform4strategy_clients.ports` defines the Protocols services depend on:
`FeatureStore`, `ModelStore`, `Broker`, `MarketData` and `Filings`, plus the
records and vocabulary they exchange: the `Filing` named tuple and the order
literals `Side`, `OrderType` and `TimeInForce`. Services
annotate their dependencies with a port, never a concrete client, and each
service's `main.py` is the one place that builds the clients from config and
passes them in. Swapping a backend (a local catalog for AWS Glue, local disk for a bucket)
is a new adapter that satisfies the port, with no service change.

`FeatureStore.read` filters with a plain mapping, `{column: value}`, where an
iterable value means "any of". Each adapter translates it to its own query.

```toml
# in a service pyproject.toml
dependencies = ["secform4strategy-clients[feature-store,messaging]"]

[tool.uv.sources]
secform4strategy-clients = { path = "../../packages/secform4strategy-clients" }  # depth varies per service
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
