"""Lambda entry point: builds the site's data files from the stack, the Alpaca account and the model store.

EventBridge Scheduler invokes it every minute with ``{"mode": "all"}``; an EventBridge rule invokes it with
``{"mode": "live"}`` when a live-score run ends. Lambda runs ``python -m awslambdaric handler.handle`` from ``src/``.
This module is the composition root: it reads the config and builds every adapter once per cold start.
"""

import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from functools import cache, partial
from typing import TYPE_CHECKING, Any

import operations
import research
from config import config
from constants import Mode, Section, file_name
from paths import SPEC_PATH
from publisher import due, publish
from pyiceberg.catalog import load_catalog
from secform4strategy_clients.broker import AlpacaClient
from secform4strategy_clients.cloudwatch import CloudWatchMetrics
from secform4strategy_clients.feature_groups import load_feature_group_catalog
from secform4strategy_clients.feature_store import IcebergFeatureStore
from secform4strategy_clients.market_data import MarketDataClient
from secform4strategy_clients.ports import PermanentError, RetryableError
from secform4strategy_clients.s3_model_store import S3ModelStore
from secform4strategy_clients.s3_site import S3Site
from secform4strategy_clients.scheduler import Scheduler
from secform4strategy_clients.sqs import SqsQueue
from secform4strategy_clients.stepfunctions import StepFunctions

if TYPE_CHECKING:
    from publisher import Outcome
    from pydantic import BaseModel
    from secform4strategy_clients.records import ModelMetadata

logger = logging.getLogger(__name__)
# The Lambda runtime installs its own log handler, so the level is set here
logging.getLogger().setLevel(logging.INFO)

executions = StepFunctions(config.aws_region)
schedules = Scheduler(config.aws_region)
queues = SqsQueue(config.aws_region)
metrics = CloudWatchMetrics(config.aws_region)
account = AlpacaClient(config.alpaca_api_key, config.alpaca_api_secret, config.alpaca_base_url)
models = S3ModelStore(config.model_store.region, config.model_store.bucket, config.model_store.prefix)
recent = IcebergFeatureStore(
    load_catalog('default'),
    config.feature_store_namespace,
    load_feature_group_catalog(SPEC_PATH, config.feature_group_version),
)
market = MarketDataClient()
site = S3Site(config.aws_region, config.site_bucket)


@cache
def model_metadata(version: str) -> ModelMetadata:
    """Return a version's metadata, read once per cold start: a saved version never changes.

    Args:
        version: The version folder.

    Returns:
        The metadata.
    """
    return models.metadata(version)


def builders(now: datetime) -> dict[Section, Callable[[], BaseModel | None]]:
    """Return the builder of every section, bound to its adapters and settings.

    The live and portfolio builders share one read of the Alpaca orders.

    Args:
        now: The invocation time every file reports.

    Returns:
        A builder per section.
    """
    spec = config.spec
    orders = cache(partial(account.orders, spec.orders_limit))
    return {
        Section.LIVE: partial(
            operations.build_live,
            now,
            operations.LiveSpec(
                config.live_score_state_machine_arn,
                config.poller_schedule_name,
                config.prediction_threshold,
                spec.live_runs,
            ),
            executions,
            schedules,
            orders,
        ),
        Section.HEALTH: partial(
            operations.build_health,
            now,
            operations.HealthSpec(
                config.name_prefix,
                spec.live_functions,
                config.live_score_state_machine_arn,
                config.retrain_state_machine_arn,
                config.poller_schedule_name,
                config.retrain_schedule_name,
            ),
            executions,
            schedules,
            queues,
            metrics,
            models,
            model_metadata,
        ),
        Section.PORTFOLIO: partial(
            operations.build_portfolio,
            now,
            operations.PortfolioSpec(spec.equity_period, spec.equity_timeframe),
            account,
            orders,
        ),
        Section.MODEL: partial(
            research.build_model,
            now,
            research.ModelSpec(config.prediction_threshold, research.ImportanceSpec(**spec.importance.model_dump())),
            site,
            models,
            models,
            recent,
            market,
        ),
        Section.EXPLORE: partial(
            research.build_explore, now, research.ExploreSpec(spec.explore.days, spec.explore.limit), recent
        ),
    }


def published(name: str) -> bool:
    """Return whether a data file exists; a failed read counts as missing, so it affects only that file's section.

    Args:
        name: The data file, such as ``explore.json``.

    Returns:
        Whether the file exists.
    """
    try:
        return site.read(name) is not None
    except PermanentError, RetryableError:
        logger.exception(f'Could not check {name}; publishing it as missing')
        return False


def handle(event: Mapping[str, Any], _context: object) -> dict[Section, Outcome]:
    """Publish the data files the event's mode calls for.

    Args:
        event: ``{"mode": "all" | "live"}``; without a mode, ``all``.
        _context: The Lambda context, unused; Lambda passes it to every handler.

    Returns:
        How each file's publication ended, by section.
    """
    now = datetime.now(UTC)
    mode = Mode(event.get('mode', Mode.ALL))
    sections = due(
        mode,
        now,
        mode is Mode.LIVE or published(file_name(Section.EXPLORE)),
        config.spec.explore.every_minutes,
    )
    bound = builders(now)
    return publish({section: bound[section] for section in sections}, site)
