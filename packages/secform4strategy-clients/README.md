# secform4strategy-clients

Pure infrastructure clients shared across the SecForm4Strategy microservices.

Each client is a thin, parameterised wrapper around an external SDK. The clients
hold **no domain knowledge** — no hardcoded feature-group names or keys, no
field parsing, no data cleaning, and they never import a service `config`.
Callers pass everything in explicitly and own all domain logic.

## Clients and extras

Every SDK lives behind an optional dependency ("extra"), and SDK imports are
lazy (performed when a client is constructed), so importing a module never pulls
a dependency you did not ask for. Install only the extras for the clients you use:

One client per external service (e.g. a single `KafkaClient` covers both
consuming and producing — not separate consumer/producer classes):

| Client | Module | Extra | SDK |
| --- | --- | --- | --- |
| `HopsworksClient` | `secform4strategy_clients.feature_store` | `feature-store` | `hopsworks` |
| `AlpacaClient` | `secform4strategy_clients.broker` | `broker` | `alpaca-trade-api` |
| `KafkaClient` | `secform4strategy_clients.messaging` | `messaging` | `quixstreams` |
| `EdgarClient` | `secform4strategy_clients.edgar` | `edgar` (+ `edgar-live` for `fetch_live_links`) | `requests` / `selenium` |
| `MarketDataClient` | `secform4strategy_clients.market_data` | `market-data` | `yfinance` |
| `LocalModelStore` | `secform4strategy_clients.model_store` | `model-store` | `joblib` |

## Ports

`secform4strategy_clients.ports` defines the Protocols services depend on:
`FeatureStore`, `ModelStore`, `Broker`, `MarketData` and `Filings`. Services
annotate their dependencies with a port, never a concrete client, and each
service's `main.py` is the one place that builds the clients from config and
passes them in. Swapping a backend (Hopsworks for S3, local disk for a bucket)
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
from secform4strategy_clients.feature_store import HopsworksClient, load_feature_group_catalog

fs = HopsworksClient("my_project", api_key, load_feature_group_catalog("feature_groups.yaml", version=1))
bt4 = fs.read("bt4", where={"acquired_disposed": "D", "ticker": ["AAPL", "MSFT"]})
fs.push("rt4", labelled)
```

`load_feature_group_catalog` validates every entry of the YAML catalog
(`primary_key`, `event_time`, optional `name`, `online_enabled`, `stream`) and
rejects unknown keys; `make validate-config` runs it over every service's catalog.
