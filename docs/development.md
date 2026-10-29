# Development

## Quality gate

`make` is the single interface.

| Target | Runs |
| --- | --- |
| `make quality-static` | ruff, pydoclint, bandit (high severity), `validate-config`; also the pre-commit hook |
| `make type-check` | mypy, per service |
| `make quality` | `format`, then `quality-static`, then `type-check` |

CI (`.github/workflows/quality.yml`) runs `quality-static` on every push to `master` and every pull request, and `type-check` as a non-blocking job until the Kafka client is removed.

The pre-commit hook (`uv run pre-commit install`; `uv run pre-commit run --all-files` to run it by hand) also checks file size (1 MB), private keys, merge markers and YAML/TOML syntax. `type-check` joins the hook once its backlog is cleared.

**mypy runs per service.** Every service has a `src` package, so a single repo-wide run sees duplicate module names and stops. Each run puts the service's modules and the clients package on `MYPYPATH`, so imports resolve to real types instead of `Any`: flat services (`from config import config`) use `<service>/src`, package services (`from src.config import config`) use `<service>`.

## Comments and docstrings

- Docstrings are short contracts: a summary line, `Args`/`Returns`/`Raises` where the tooling requires them, at most one sentence of why. Types live in signatures, not in docstrings.
- Comments are single lines. Anything longer goes into the docstring of the module, class or function it explains, or into `docs/`.
- No commented-out code; history lives in git.

## Docker builds

Services that depend on the shared clients package build with the repo root as context and replicate the repo layout under `/repo`, so the `[tool.uv.sources]` path from the service's `pyproject.toml` resolves inside the image exactly as on the host. The clients package and the service's `pyproject.toml`/`uv.lock` are copied before the source, so the dependency layer is cached across code changes.

`.dockerignore` keeps host build artifacts (virtualenvs, caches) out of the context: copied over the image's environment they would clobber what `uv sync` built and break `uv run --no-sync`.

## Shared clients package

`packages/secform4strategy-clients` has no hard dependencies. Every SDK sits behind an extra and is imported lazily, so a service installs and imports only the clients it uses. Its `requires-python` range is permissive because each service resolves the package against its own interpreter.

Services depend on the Protocols in `secform4strategy_clients.ports`, never on concrete clients; each `main.py` builds the clients from config and passes them in.

## Local Kafka (Redpanda)

The broker advertises two Kafka addresses: `internal://redpanda-0:9092` for clients inside the `redpanda-network` Docker network, `external://localhost:19092` for clients on the host.
