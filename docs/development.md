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

## Tests

Each project tests itself in its own environment: `<service>/tests/` beside its `src/`, pytest in the project's `dev` dependency group, and settings in its `pyproject.toml` (`--strict-markers`, `--strict-config`; flat services put `src` and the service root on the path). `make test-unit` runs every project's unit tests with coverage, `make test-unit-fast` without; `make quality-static`, and so the pre-commit hook, runs the fast one. A project without test files yet is skipped.

- **Tiers:** `tests/unit/` is fast and offline, marked `unit` by each `tests/conftest.py`. Integration tests (a real Iceberg catalog, SEC, Yahoo) get `tests/integration/` when they arrive.
- **Fakes, never mocks:** typed in-memory implementations of the ports live in `secform4strategy_clients.fakes`, one module per port so each needs only its port's extra. Each ends with `_conforms: Port = Fake(...)`, so `make type-check` fails when a fake drifts from its Protocol. Fakes record their calls; a fake arrives when a test needs it.
- **Test data:** builders in `tests/factories.py` default every field, so a test names only what it is about; real external data (Form 4 submissions) is saved under `tests/fixtures/` and located through `tests/paths.py`, each fixture chosen for one rule.
- **Config at import:** modules that read the service config at import (each `main.py`) get test values from `tests/conftest.py`.
- **Shape:** a module docstring states the behaviour and names the code `Under test`; `class Test...` groups one rule with a docstring; test names are statements (no docstring needed).

## Comments and docstrings

- Docstrings are short contracts: a summary line, `Args`/`Returns`/`Raises` where the tooling requires them, at most one sentence of why. Types live in signatures, not in docstrings.
- Comments are single lines. Anything longer goes into the docstring of the module, class or function it explains, or into `docs/`.
- No commented-out code; history lives in git.

## Docker builds

Services that depend on the shared clients package build with the repo root as context and replicate the repo layout under `/repo`, so the `[tool.uv.sources]` path from the service's `pyproject.toml` resolves inside the image exactly as on the host. The clients package and the service's `pyproject.toml`/`uv.lock` are copied before the source, so the dependency layer is cached across code changes.

`.dockerignore` keeps host build artifacts (virtualenvs, caches) out of the context: copied over the image's environment they would clobber what `uv sync` built and break `uv run --no-sync`.

## Shared clients package

`packages/secform4strategy-clients` is installed editable in every service (`editable = true` in `[tool.uv.sources]`), so a local environment always runs the package source; a non-editable install keeps a stale copy until the lockfile changes. It has no hard dependencies. Every SDK sits behind an extra and is imported at the top of its one client module, so a service installs and imports only the client modules it uses; the package `__init__` imports nothing. Its `requires-python` range is permissive because each service resolves the package against its own interpreter.

Services depend on the Protocols in `secform4strategy_clients.ports`, never on concrete clients; each `main.py` builds the clients from config and passes them in.

## Local Kafka (Redpanda)

The broker advertises two Kafka addresses: `internal://redpanda-0:9092` for clients inside the `redpanda-network` Docker network, `external://localhost:19092` for clients on the host.
