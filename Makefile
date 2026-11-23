.PHONY: lint format type-check security docstrings validate-config test-unit test-unit-fast quality quality-static \
	start-redpanda stop-redpanda inference-sec inference-prices inference-model training-sec training-prices \
	training-model lakehouse-summary switch-on switch-off status

CLIENTS_SRC := packages/secform4strategy-clients/src
# Services importing flat from src/ (`from config import config`)
FLAT_SERVICES := training inference sec-feature-pipeline/master-index sec-feature-pipeline/scraper \
	sec-feature-pipeline/kafka-to-store site-publisher
# Services importing through the src package (`from src.config import config`)
PACKAGE_SERVICES := prices-feature-pipeline/target prices-feature-pipeline/yahoo_connect
# Every project with its own environment, in test order
PROJECTS := packages/secform4strategy-clients $(FLAT_SERVICES) $(PACKAGE_SERVICES)
# Extra pytest arguments, e.g. make test-unit PYTEST_ARGS='-k next_print'
PYTEST_ARGS ?=

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
# Every spec.yaml feature-group catalog parses against the FeatureGroupSpec schema
validate-config:
	PYTHONPATH=$(CLIENTS_SRC) uv run python -c "import glob, pathlib; \
	from secform4strategy_clients.feature_groups import load_feature_group_catalog as load; \
	[print(p, sorted(load(pathlib.Path(p), 1))) for p in sorted(glob.glob('**/src/spec.yaml', recursive=True))]"
# Unit tests run per project in its own environment; projects without test files yet are skipped
test-unit:
	@status=0; for svc in $(PROJECTS); do \
		if [ -n "$$(find $$svc/tests/unit -name 'test_*.py' 2>/dev/null)" ]; then \
			echo "--- $$svc"; (cd $$svc && uv run --all-extras pytest tests/unit --cov=src --cov-report=term-missing:skip-covered $(PYTEST_ARGS)) || status=1; \
		fi; \
	done; exit $$status
test-unit-fast:
	@status=0; for svc in $(PROJECTS); do \
		if [ -n "$$(find $$svc/tests/unit -name 'test_*.py' 2>/dev/null)" ]; then \
			echo "--- $$svc"; (cd $$svc && uv run --all-extras pytest tests/unit -q -p no:cacheprovider $(PYTEST_ARGS)) || status=1; \
		fi; \
	done; exit $$status
# Fast checks run by the pre-commit hook; type-check joins once its backlog is cleared
quality-static: lint docstrings security validate-config test-unit-fast
quality: format quality-static type-check
	@echo "Quality checks complete."


#Running docker containers for redpanda cluster
start-redpanda:
	docker-compose -f docker-compose/redpanda.yml up -d
stop-redpanda:
	docker-compose -f docker-compose/redpanda.yml down


inference-sec:
	docker-compose -f docker-compose/system-inference/services-inference.yml \
	--profile sec up --build
inference-prices:
	docker-compose -f docker-compose/system-inference/services-inference.yml \
	--profile prices up --build

training-sec:
	docker-compose -f docker-compose/system-training/services-training.yml \
	--profile sec up --build
training-prices:
	docker-compose -f docker-compose/system-training/services-training.yml \
	--profile prices up --build
training-model:
	GIT_SHA=$$(git rev-parse HEAD) docker-compose -f docker-compose/system-training/services-training.yml \
	--profile model up --build
inference-model:
	docker-compose -f docker-compose/system-inference/services-inference.yml \
	--profile model up --build

# Row counts, time spans and the latest model rows of every feature-store table
lakehouse-summary:
	docker-compose -f docker-compose/tools.yml run --rm --build lakehouse-summary

# One screen of the AWS stack: switches, runs, queues with rates and ETAs, tasks, errors, model (docs/aws.md)
status:
	@AWS_PROFILE=$${AWS_PROFILE:-secform4} uv run --quiet scripts/aws_status.py


# The AWS stack's on/off switch: commits the switch file and pushes master; CI applies it once you approve (docs/aws.md)
SWITCH_FILE := infra/envs/demo/switch.auto.tfvars
switch-on switch-off:
	@test "$$(git branch --show-current)" = master || { echo 'Switch from master: CI applies only master'; exit 1; }
	@git diff --quiet && git diff --cached --quiet || { echo 'Commit or stash your changes first'; exit 1; }
	@git pull --ff-only --quiet
	@sed -i.bak 's/^switched_on\( *\)= .*/switched_on\1= $(if $(filter switch-on,$@),true,false)/' $(SWITCH_FILE) && rm -f $(SWITCH_FILE).bak
	@if git diff --quiet -- $(SWITCH_FILE); then echo 'The stack is already $(subst switch-,,$@)'; else \
		git commit --quiet -m 'chore: switch the stack $(subst switch-,,$@)' -- $(SWITCH_FILE) && git push --quiet && \
		echo 'Pushed; approve the apply in GitHub: Actions > terraform'; fi
