"""Live performance counts each row once, marks each short at its position's price, and rates only labelled rows.

A short gains when the price falls below its fill. A row is a short winner when its label is below the label return
of the model that scored it.

Under test:
    inference/src/performance.py
"""

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from performance import Bucket, Trade, calibration, measure, short_pnl, short_winners
from secform4strategy_clients.records import BrokerOrder, BrokerPosition

T0 = datetime(2026, 11, 23, 14, 30, tzinfo=UTC)
V1, V2 = '20261122T210000Z', '20261129T210000Z'


def predictions(*rows: tuple[str, float, str, int]) -> pd.DataFrame:
    """Records of (row key, probability, model version, minutes after T0), shorted at 0.5."""
    return pd.DataFrame(
        {
            'row_key': [row[0] for row in rows],
            'probability': [row[1] for row in rows],
            'model_version': [row[2] for row in rows],
            'scored_at': [T0 + timedelta(minutes=row[3]) for row in rows],
            'shorted': [row[1] >= 0.5 for row in rows],
        }
    )


def sell(key: str, symbol: str, fill: float | None, side: str = 'sell') -> BrokerOrder:
    """A one-share order under ``key``, filled at ``fill`` when given."""
    return BrokerOrder(f'id-{key}', key, symbol, side, 1.0, 'filled' if fill else 'new', T0, T0 if fill else None, fill)


def position(symbol: str, price: float | None) -> BrokerPosition:
    """A one-share short of ``symbol`` marked at ``price``."""
    return BrokerPosition(symbol, -1.0, 100.0, price, None, None, None)


class TestShortPnl:
    """A short gains the fall from its fill; nothing is known before the fill and the mark."""

    def test_a_fall_is_a_gain(self) -> None:
        assert short_pnl(100.0, 90.0, 2.0) == (20.0, 0.1)

    def test_a_rise_is_a_loss(self) -> None:
        assert short_pnl(100.0, 110.0, 1.0) == (-10.0, -0.1)

    @pytest.mark.parametrize(('fill', 'mark'), [(None, 90.0), (100.0, None)])
    def test_unfilled_or_unmarked_is_unknown(self, fill: float | None, mark: float | None) -> None:
        assert short_pnl(fill, mark, 1.0) == (None, None)


class TestShortWinners:
    """A label below the model's label return is a winner; a missing label or label return is unknown."""

    def test_each_case(self) -> None:
        latest = predictions(('a', 0.6, V1, 0), ('b', 0.6, V1, 0), ('c', 0.6, V1, 0), ('d', 0.6, V2, 0))
        winners = short_winners(latest, {'a': -0.10, 'b': -0.01, 'd': -0.20}, {V1: -0.05, V2: None})
        assert winners.tolist() == [True, False, pd.NA, pd.NA]


class TestCalibration:
    """Labelled rows fall in tenths of probability, the top bucket closed at 1."""

    def test_buckets(self) -> None:
        probabilities = pd.Series([0.15, 0.18, 0.95, 1.0, 0.5])
        winners = pd.Series([False, True, True, True, pd.NA], dtype='boolean')
        assert calibration(probabilities, winners) == (
            Bucket(0.1, 0.2, 2, pytest.approx(0.165), 0.5),
            Bucket(0.9, 1.0, 2, pytest.approx(0.975), 1.0),
        )

    def test_no_labelled_rows_have_no_buckets(self) -> None:
        assert calibration(pd.Series([0.3]), pd.Series([pd.NA], dtype='boolean')) == ()


class TestMeasure:
    """Rows count once at their latest scoring; trades are the sell orders the predictions name."""

    PREDICTIONS = predictions(
        ('AAA@1', 0.4, V1, 0),
        ('AAA@1', 0.7, V1, 5),
        ('BBB@1', 0.8, V1, 0),
        ('CCC@1', 0.9, V1, 0),
        ('DDD@1', 0.1, V1, 0),
    )
    ORDERS = (
        sell('AAA@1', 'AAA', 100.0),
        sell('BBB@1', 'BBB', None),
        sell('CCC@1', 'CCC', 50.0),
        sell('manual', 'EEE', 10.0),
        sell('AAA@1', 'AAA', 99.0, side='buy'),
    )
    POSITIONS = (position('AAA', 90.0), position('CCC', 55.0))

    def test_counts_and_pnl(self) -> None:
        result = measure(self.PREDICTIONS, self.ORDERS, self.POSITIONS, {}, {V1: -0.05})
        assert (result.scored, result.signals, result.orders, result.filled, result.unrecorded_orders) == (
            4,
            3,
            3,
            2,
            1,
        )
        assert result.total_pnl == pytest.approx(5.0)
        assert result.trades[0] == Trade('AAA@1', 'AAA', 0.7, 'filled', 1.0, 100.0, 90.0, 10.0, 0.1)
        assert (result.trades[1].pnl, result.trades[1].mark) == (None, None)

    def test_rates_count_labelled_rows_only(self) -> None:
        labels = {'AAA@1': -0.10, 'BBB@1': 0.02, 'DDD@1': -0.30}
        result = measure(self.PREDICTIONS, self.ORDERS, self.POSITIONS, labels, {V1: -0.05})
        assert (result.labelled, result.labelled_signals, result.hit_rate) == (3, 2, 0.5)
        assert result.base_rate == pytest.approx(2 / 3)

    def test_without_labels_the_rates_are_unknown(self) -> None:
        result = measure(self.PREDICTIONS, (), (), {}, {V1: -0.05})
        assert (result.labelled, result.hit_rate, result.base_rate, result.calibration) == (0, None, None, ())
