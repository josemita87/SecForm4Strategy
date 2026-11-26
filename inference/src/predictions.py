"""Scored rows as ``Prediction`` records, upserted into the ``predictions`` feature group after the orders."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, NamedTuple

import pandas as pd
from constants import Col, FeatureGroup
from records import Prediction

if TYPE_CHECKING:
    from constants import OrderOutcome, Source
    from inference_pipeline import LoadedModel
    from secform4strategy_clients.ports import FeatureStore


class ScoringRun(NamedTuple):
    """Which path scored the rows, when, and the run's id where it has one."""

    source: Source
    scored_at: datetime
    run_id: str | None


def prediction_records(
    scored: pd.DataFrame, model: LoadedModel, outcomes: Mapping[str, OrderOutcome], run: ScoringRun
) -> tuple[Prediction, ...]:
    """Return one record per scored row, with the outcome of its order if it was shorted.

    Args:
        scored: Rows from ``score`` with ``key``, ``ticker``, ``accepted_at`` and ``probability``.
        model: The model that scored them.
        outcomes: Each shorted row key's order outcome, from ``execute_trades``.
        run: The scoring run.

    Returns:
        The records, in the rows' order.
    """
    return tuple(
        Prediction(
            row_key=key,
            ticker=ticker,
            accepted_at=pd.to_datetime(accepted_at, utc=True),
            probability=probability,
            probability_cutoff=model.probability_cutoff,
            model_version=model.version,
            source=run.source,
            scored_at=run.scored_at,
            run_id=run.run_id,
            order_outcome=outcomes.get(key),
        )
        for key, ticker, accepted_at, probability in scored[
            [Col.KEY, Col.TICKER, Col.ACCEPTED_AT, Col.PROBABILITY]
        ].itertuples(index=False)
    )


def write_predictions(store: FeatureStore, records: Sequence[Prediction]) -> None:
    """Upsert the records into the ``predictions`` feature group; none writes nothing.

    The nullable text columns are typed as strings, so a first push whose values are all None still creates them
    as strings.

    Args:
        store: The feature store.
        records: The predictions.
    """
    store.push(
        FeatureGroup.PREDICTIONS,
        pd.DataFrame([record.model_dump() for record in records]).astype(
            dict.fromkeys((Col.RUN_ID, Col.CLIENT_ORDER_ID, Col.ORDER_OUTCOME), 'string')
        )
        if records
        else pd.DataFrame(),
    )
