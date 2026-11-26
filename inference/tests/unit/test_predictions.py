"""Each scored row becomes one prediction record, upserted after the orders under a key a rerun reuses.

A new model adds records beside the old ones; a write whose commit retries run out raises ``RetryableError``, so the
run is retried and its placed orders are not repeated.

Under test:
    inference/src/records.py
    inference/src/predictions.py
"""

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest
from constants import OrderOutcome, Source
from inference_pipeline import LoadedModel
from predictions import ScoringRun, prediction_records, write_predictions
from pyiceberg.catalog.sql import SqlCatalog
from secform4strategy_clients.fakes.feature_store import FakeFeatureStore
from secform4strategy_clients.feature_groups import FeatureGroupSpec
from secform4strategy_clients.feature_store import IcebergFeatureStore
from secform4strategy_clients.ports import RetryableError
from sklearn.pipeline import Pipeline

SCORED = pd.DataFrame(
    {
        'key': ['AAA@2026-11-23T14:30Z', 'BBB@2026-11-23T14:30Z'],
        'ticker': ['AAA', 'BBB'],
        'accepted_at': pd.to_datetime(['2026-11-23T14:00Z', '2026-11-23T14:05Z']),
        'probability': [0.2, 0.7],
    }
)
MODEL = LoadedModel(Pipeline([('passthrough', 'passthrough')]), (), 0.5, '20261122T210000Z')
LIVE = ScoringRun(Source.LIVE, datetime(2026, 11, 23, 14, 6, tzinfo=UTC), 'request-1')
OUTCOMES = {'BBB@2026-11-23T14:30Z': OrderOutcome.SUBMITTED}


class TestRecords:
    """One record per row: keyed by source, model and row; shorted at the cutoff under the row key."""

    def test_the_key_joins_source_model_and_row(self) -> None:
        assert [record.key for record in prediction_records(SCORED, MODEL, OUTCOMES, LIVE)] == [
            'live|20261122T210000Z|AAA@2026-11-23T14:30Z',
            'live|20261122T210000Z|BBB@2026-11-23T14:30Z',
        ]

    def test_a_short_carries_its_order(self) -> None:
        record = prediction_records(SCORED, MODEL, OUTCOMES, LIVE)[1].model_dump()
        assert {name: record[name] for name in ('shorted', 'client_order_id', 'order_outcome', 'run_id')} == {
            'shorted': True,
            'client_order_id': 'BBB@2026-11-23T14:30Z',
            'order_outcome': 'submitted',
            'run_id': 'request-1',
        }

    def test_a_row_below_the_cutoff_has_no_order(self) -> None:
        record = prediction_records(SCORED, MODEL, OUTCOMES, LIVE)[0]
        assert (record.shorted, record.client_order_id, record.order_outcome) == (False, None, None)

    def test_a_naive_acceptance_time_is_read_as_utc(self) -> None:
        naive = SCORED.assign(accepted_at=SCORED['accepted_at'].dt.tz_localize(None))
        assert prediction_records(naive, MODEL, OUTCOMES, LIVE)[0].accepted_at == datetime(2026, 11, 23, 14, tzinfo=UTC)


class TestWrite:
    """Reruns replace their records, a new model adds its own, and a write that keeps conflicting is retried later."""

    def test_a_rerun_replaces_its_records(self) -> None:
        store = FakeFeatureStore()
        write_predictions(store, prediction_records(SCORED, MODEL, OUTCOMES, LIVE))
        retry = {'BBB@2026-11-23T14:30Z': OrderOutcome.PLACED_BEFORE}
        write_predictions(store, prediction_records(SCORED, MODEL, retry, LIVE._replace(run_id='request-2')))
        table = store.read('predictions')
        assert (len(table), set(table['run_id']), table['order_outcome'].dropna().tolist()) == (
            2,
            {'request-2'},
            ['placed_before'],
        )

    def test_a_new_model_adds_records(self) -> None:
        store = FakeFeatureStore()
        write_predictions(store, prediction_records(SCORED, MODEL, OUTCOMES, LIVE))
        write_predictions(store, prediction_records(SCORED, MODEL._replace(version='20261129T210000Z'), {}, LIVE))
        assert store.read('predictions', where={'row_key': 'AAA@2026-11-23T14:30Z'})['model_version'].tolist() == [
            '20261122T210000Z',
            '20261129T210000Z',
        ]

    def test_no_records_write_nothing(self) -> None:
        store = FakeFeatureStore()
        write_predictions(store, ())
        assert store.pushes == []

    def test_a_write_out_of_commit_retries_is_retryable(self) -> None:
        store = FakeFeatureStore(conflicts=1)
        with pytest.raises(RetryableError, match='commit conflict'):
            write_predictions(store, prediction_records(SCORED, MODEL, OUTCOMES, LIVE))
        write_predictions(store, prediction_records(SCORED, MODEL, OUTCOMES, LIVE))
        assert len(store.read('predictions')) == 2

    def test_a_first_batch_write_creates_typed_columns(self, tmp_path: Path) -> None:
        catalog = SqlCatalog('test', uri=f'sqlite:///{tmp_path}/catalog.db', warehouse=tmp_path.as_uri())
        spec = FeatureGroupSpec(name='predictions', version=1, primary_key=('key',), event_time='scored_at')
        store = IcebergFeatureStore(catalog, 'features', {'predictions': spec})
        write_predictions(store, prediction_records(SCORED, MODEL, {}, ScoringRun(Source.BATCH, LIVE.scored_at, None)))
        write_predictions(store, prediction_records(SCORED, MODEL, OUTCOMES, LIVE))
        schema = catalog.load_table('features.predictions_v1').schema()
        assert {name: str(schema.find_field(name).field_type) for name in ('run_id', 'order_outcome', 'scored_at')} == {
            'run_id': 'string',
            'order_outcome': 'string',
            'scored_at': 'timestamptz',
        }
        assert len(store.read('predictions')) == 4
