.PHONY: lint format type-check security docstrings validate-config quality quality-static \
	start-redpanda stop-redpanda inference-sec inference-prices training-sec training-prices

CLIENTS_SRC := packages/secform4strategy-clients/src
# Services importing flat from src/ (`from config import config`); api-enricher is excluded
FLAT_SERVICES := training inference sec-feature-pipeline/master-index sec-feature-pipeline/scraper \
	sec-feature-pipeline/kafka-to-store
# Services importing through the src package (`from src.config import config`)
PACKAGE_SERVICES := prices-feature-pipeline/target prices-feature-pipeline/yahoo_connect

# --- Code quality (shared root config in pyproject.toml, applied repo-wide) ---
lint:
	uv run ruff check .
format:
	uv run ruff format .
	uv run ruff check . --fix
# One mypy run per service (docs/development.md)
type-check:
	@status=0; \
	MYPYPATH=$(CLIENTS_SRC) uv run mypy --explicit-package-bases $(CLIENTS_SRC)/secform4strategy_clients || status=1; \
	for svc in $(FLAT_SERVICES); do \
		echo "--- $$svc"; MYPYPATH=$$svc/src:$(CLIENTS_SRC) uv run mypy --explicit-package-bases $$svc/src || status=1; \
	done; \
	for svc in $(PACKAGE_SERVICES); do \
		echo "--- $$svc"; MYPYPATH=$$svc:$(CLIENTS_SRC) uv run mypy --explicit-package-bases $$svc/src || status=1; \
	done; \
	exit $$status
docstrings:
	uv run pydoclint .
security:
	uv run bandit -c pyproject.toml -r . --severity-level high
# Every feature_groups.yaml catalog parses against the FeatureGroupSpec schema
validate-config:
	PYTHONPATH=$(CLIENTS_SRC) uv run python -c "import glob; \
	from secform4strategy_clients.feature_store import load_feature_group_catalog as load; \
	[print(p, sorted(load(p, 1))) for p in sorted(glob.glob('**/feature_groups.yaml', recursive=True))]"
# Fast checks run by the pre-commit hook; type-check joins once its backlog is cleared
quality-static: lint docstrings security validate-config
quality: format quality-static type-check
	@echo "Quality checks complete."


#Running docker containers for redpanda cluster
start-redpanda:
	docker-compose -f redpanda.yml up -d
stop-redpanda:
	docker-compose -f redpanda.yml down


inference-sec:
	docker-compose -f docker-compose/system-inference/services-inference.yml \
	--profile sec up
inference-prices:
	docker-compose -f docker-compose/system-inference/services-inference.yml \
	--profile prices up

training-sec:
	docker-compose -f docker-compose/system-training/services-training.yml \
	--profile sec up
training-prices:
	docker-compose -f docker-compose/system-training/services-training.yml \
	--profile prices up
