# SecForm4Strategy

SEC Form 4 insider-trading ML system: Python 3.14 microservices (one uv project each) plus the shared `packages/secform4strategy-clients`. Design notes live in `docs/` (`configuration.md`, `development.md`).

## Commands

```bash
make quality-static   # ruff, pydoclint, bandit, validate-config (also the pre-commit hook)
make type-check       # mypy, one run per service
make quality          # format + quality-static + type-check
make validate-config  # every spec.yaml feature-group catalog against FeatureGroupSpec
```

## Comments and docstrings

- **Comments are single lines.** A comment that needs more than one line moves into the docstring of the module, class or function it explains when it is part of that contract; otherwise into `docs/` (an existing file, or a new one for a new topic), leaving at most a one-line pointer.
- **No commented-out code.** Delete it; history lives in git.
- **Docstrings are short contracts:** summary line, `Args`/`Returns`/`Raises` where pydoclint requires them, at most one sentence of why. Types live in signatures, never in docstrings.
- Comments and docstrings state what holds now, not what changed.

## Code

- **Config is read only in `main.py`.** Components take the specific values and ports they need; the caller picks the config fields. Three or more related parameters become a frozen dataclass (e.g. `OrderSpec`). Library modules never import the config singleton or build clients at import time.
- **Ports:** services annotate dependencies with the Protocols in `secform4strategy_clients.ports`, never concrete clients; each `main.py` is the composition root.
- **Condensed:** walrus where it removes a line, comprehensions over loops, no rebind aliases (`qty = config.qty` just to pass it on).
- **Types:** native generics (`list`, `dict`, `X | None`), `collections.abc` for abstract types; the most specific type a library offers (`httpx.Headers`, `Path`, `datetime`, `Literal` vocabularies) over `str`/`object`. No redundant conversions (`dict(x)`, `list(x)`) unless an API requires that type.
- **Records:** fixed-shape records are `NamedTuple`s (or frozen dataclasses / pydantic models when validated), never ad-hoc string-keyed dicts. A validated model resolves derived fields in validators or `computed_field`s, so `model_dump()` is the record; no hand-built `to_dict()`. Models are data containers (at most light identification calculations); processing lives in a service module. External formats (e.g. Form 4 XML) are declared as models, not path-string tables.
- **Immutability:** sequences that are not mutated afterwards are tuples; config list fields are `tuple[str, ...]`.
- **Inline single-use names:** a constant or local used once is inlined at its use site.
- **Imports at module top.** No imports inside functions; each client module imports its SDK at the top (one module per extra). `if TYPE_CHECKING:` only for third-party or first-party names used solely in annotations; standard-library imports always stay at the top.
- **Secrets:** credentials are `SecretStr` in config and stay wrapped until the adapter hands them to its SDK (`get_secret_value()` at that call only). SEC requests carry one honest `User-Agent` from `SEC_USER_AGENT`.
- **Constants:** enums over raw strings, including DataFrame columns (a `Column(StrEnum)` per service); static data in YAML specs, validated at load.
- **Tests:** `unittest.mock` and `pytest_mock` are banned; substitute typed in-memory port implementations.

## Git

- Commit only when explicitly asked; stage and report otherwise.
- Commit messages follow the `git` skill (`.claude/skills/git`), no `Co-Authored-By` trailers.
