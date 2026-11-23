"""Inference scores rows with gaps as training does, shorts those at the cutoff, and refuses a model without one.

Under test:
    inference/src/inference_pipeline.py
"""

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import pytest
import sklearn
from inference_pipeline import InferencePipeline, OrderSpec, score
from secform4strategy_clients.fakes.models import FakeModelStore
from secform4strategy_clients.records import ModelMetadata, Scores
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

if TYPE_CHECKING:
    from secform4strategy_clients.ports import Broker, OrderType, Side, TimeInForce

#: Training rows with gaps, as the feature store gives them; a short winner has a positive size or no exchange
TRAIN = pd.DataFrame({'size': [-2.0, -1.0, 1.0, 2.0, np.nan] * 12, 'exchange': ['NYSE', 'NASDAQ', None] * 20})
MODEL = Pipeline(
    [
        (
            'preprocess',
            ColumnTransformer(
                [
                    ('num', StandardScaler(), [0]),
                    ('cat', OneHotEncoder(handle_unknown='ignore', sparse_output=False), [1]),
                ]
            ),
        ),
        ('classifier', HistGradientBoostingClassifier(max_iter=50, min_samples_leaf=1, random_state=0)),
    ]
).fit(TRAIN.to_numpy(), ((TRAIN['size'] > 0) | TRAIN['exchange'].isna()).to_numpy())
FEATURES = ('size', 'exchange')
METADATA = ModelMetadata(
    trained_at=datetime(2026, 11, 22, 21, 0, tzinfo=UTC),
    git_sha='abc123',
    sklearn_version=sklearn.__version__,
    feature_group_version=1,
    target='pct_change',
    features=FEATURES,
    model_params={},
    train_rows=100,
    test_rows=20,
    test_start=datetime(2026, 10, 1, tzinfo=UTC),
    metrics={'model': Scores(short_return=0.02, n_shorts=5, average_precision=0.4)},
    probability_cutoff=0.5,
)
ROWS = pd.DataFrame(
    {
        'key': ['k1', 'k2', 'k3'],
        'ticker': ['AAA', 'BBB', 'CCC'],
        'accepted_at': pd.to_datetime(['2026-11-23T14:00Z'] * 3),
        'size': [-2.0, 2.0, np.nan],
        'exchange': ['NYSE', 'NYSE', 'NASDAQ'],
    }
)
ORDER = OrderSpec(qty=1.0, side='sell', order_type='market', time_in_force='gtc')


@dataclass
class RecordingBroker:
    """A broker that records each order's symbol and client order id."""

    placed: list[tuple[str, str]] = field(default_factory=list)

    def place_order(
        self,
        symbol: str,
        qty: float,
        side: Side,
        order_type: OrderType,
        time_in_force: TimeInForce,
        client_order_id: str,
    ) -> object | None:
        """Record the order and return it."""
        self.placed.append((symbol, client_order_id))
        return client_order_id


_conforms: Broker = RecordingBroker()


def pipeline(metadata: ModelMetadata) -> tuple[InferencePipeline, RecordingBroker]:
    """An inference pipeline over a store whose promoted model has ``metadata``, and its broker."""
    store, broker = FakeModelStore(), RecordingBroker()
    store.promote(store.save(MODEL, metadata))
    return InferencePipeline(broker, store), broker


class TestLoadModel:
    """The cutoff comes from the metadata unless overridden; a model without one is refused."""

    def test_the_metadata_cutoff_is_used(self) -> None:
        assert pipeline(METADATA)[0].load_model().probability_cutoff == 0.5

    def test_an_override_replaces_it(self) -> None:
        assert pipeline(METADATA)[0].load_model(0.7).probability_cutoff == 0.7

    def test_a_model_without_a_cutoff_is_refused(self) -> None:
        with pytest.raises(RuntimeError, match='probability_cutoff'):
            pipeline(replace(METADATA, probability_cutoff=None))[0].load_model(0.7)

    def test_another_sklearn_version_is_refused(self) -> None:
        with pytest.raises(RuntimeError, match='scikit-learn'):
            pipeline(replace(METADATA, sklearn_version='0.0.1'))[0].load_model()


class TestScore:
    """Rows with missing features are scored as training scored them; rows that cannot be identified are dropped."""

    def test_every_identifiable_row_gets_a_probability(self) -> None:
        scored = score(MODEL, FEATURES, pd.concat([ROWS, ROWS]))
        assert scored['key'].tolist() == ['k1', 'k2', 'k3']
        assert scored['probability'].between(0, 1).all()

    def test_a_row_missing_numeric_and_categorical_features_is_scored(self) -> None:
        scored = score(MODEL, FEATURES, ROWS.assign(size=np.nan, exchange=None))
        assert scored['probability'].gt(0.5).all()

    def test_a_missing_category_as_nan_is_encoded_as_training_saw_it(self) -> None:
        as_none = score(MODEL, FEATURES, ROWS.assign(exchange=None))['probability']
        assert score(MODEL, FEATURES, ROWS.assign(exchange=np.nan))['probability'].equals(as_none)
        assert as_none.gt(0.5).all()

    @pytest.mark.parametrize('column', ['key', 'ticker', 'accepted_at'])
    def test_a_row_missing_its_identity_is_dropped(self, column: str) -> None:
        rows = ROWS.astype({column: object})
        rows.loc[0, column] = None
        assert score(MODEL, FEATURES, rows)['key'].tolist() == [*ROWS['key'][1:]]

    def test_no_identifiable_row_scores_nothing(self) -> None:
        assert score(MODEL, FEATURES, ROWS.assign(ticker=None)).empty


class TestTrades:
    """Rows at or above the cutoff are shorted under their key."""

    def test_rows_at_or_above_the_cutoff_are_shorted(self) -> None:
        inference, broker = pipeline(METADATA)
        loaded = inference.load_model()
        assert inference.execute_trades(score(loaded.pipeline, loaded.features, ROWS), 0.5, ORDER) == []
        assert broker.placed == [('BBB', 'k2')]

    def test_a_cutoff_above_every_probability_shorts_nothing(self) -> None:
        inference, broker = pipeline(METADATA)
        inference.execute_trades(score(MODEL, FEATURES, ROWS), 0.999, ORDER)
        assert broker.placed == []
