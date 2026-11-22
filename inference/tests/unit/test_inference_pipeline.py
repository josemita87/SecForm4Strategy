"""Inference shorts the rows whose probability reaches the model's cutoff, and refuses a model without one.

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
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

if TYPE_CHECKING:
    from secform4strategy_clients.ports import Broker, OrderType, Side, TimeInForce

#: One feature; the probability of a short winner rises with it
X = np.array([[-2.0], [-1.0], [1.0], [2.0]])
MODEL = Pipeline([('classifier', LogisticRegression())]).fit(X, np.array([False, False, True, True]))
METADATA = ModelMetadata(
    trained_at=datetime(2026, 11, 22, 21, 0, tzinfo=UTC),
    git_sha='abc123',
    sklearn_version=sklearn.__version__,
    feature_group_version=1,
    target='pct_change',
    features=('size',),
    model_params={},
    train_rows=100,
    test_rows=20,
    test_start=datetime(2026, 10, 1, tzinfo=UTC),
    metrics={'model': Scores(short_return=0.02, n_shorts=5, average_precision=0.4)},
    probability_cutoff=0.5,
)
ROWS = pd.DataFrame({'key': ['k1', 'k2', 'k3'], 'ticker': ['AAA', 'BBB', 'CCC'], 'size': [-2.0, 2.0, np.nan]})
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


class TestTrades:
    """Complete rows are scored; those at or above the cutoff are shorted under their key."""

    def test_complete_rows_get_a_probability(self) -> None:
        scored = score(MODEL, ('size',), ROWS)
        assert scored['key'].tolist() == ['k1', 'k2']
        assert scored['probability'].between(0, 1).all()

    def test_rows_at_or_above_the_cutoff_are_shorted(self) -> None:
        inference, broker = pipeline(METADATA)
        loaded = inference.load_model()
        assert inference.execute_trades(score(loaded.pipeline, loaded.features, ROWS), 0.5, ORDER) == []
        assert broker.placed == [('BBB', 'k2')]

    def test_a_cutoff_above_every_probability_shorts_nothing(self) -> None:
        inference, broker = pipeline(METADATA)
        inference.execute_trades(score(MODEL, ('size',), ROWS), 0.999, ORDER)
        assert broker.placed == []
