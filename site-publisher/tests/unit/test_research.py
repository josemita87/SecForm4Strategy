"""model.json and explore.json: the holdout replayed against SPY, and the recent insider sales.

Under test:
    site-publisher/src/research.py
"""

import json
from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest
from research import (
    ImportanceSpec,
    clusters,
    cumulative,
    explore_report,
    folds,
    holdout,
    importance,
    largest_sales,
    model_report,
    published_version,
    role,
    sales,
    scored,
    threshold_table,
)
from secform4strategy_clients.records import ModelMetadata, Scores
from sklearn.linear_model import LinearRegression

# model.json

TRAINED = datetime(2026, 11, 20, 21, 6, 17, tzinfo=UTC)
TEST_START = datetime(2026, 6, 1, tzinfo=UTC)


def metadata(**overrides: object) -> ModelMetadata:
    fields: dict[str, object] = {
        'trained_at': TRAINED,
        'git_sha': '19a068a5aa6f',
        'sklearn_version': '1.9.1',
        'feature_group_version': 1,
        'target': 'pct_change',
        'features': ('a', 'b'),
        'model_params': {'learning_rate': 0.03},
        'train_rows': 10,
        'test_rows': 4,
        'test_start': TEST_START,
        'metrics': {'model': Scores(0.12, 0.1, 3), 'zero': Scores(0.11, 0.0, 0), 'train_mean': Scores(0.2, 0.0, 0)},
    }
    return ModelMetadata(**(fields | overrides))  # type: ignore[arg-type]


def rows_scored(accepted: list[str], predicted: list[float], actual: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            'ticker': [f'T{i}' for i in range(len(accepted))],
            'accepted_at': pd.to_datetime(accepted, utc=True),
            'predicted': predicted,
            'actual': actual,
        }
    )


class TestHoldout:
    """The holdout is rows accepted from test_start and labelled before training; missing features stay."""

    def test_rows_outside_or_unlabelled_are_dropped(self) -> None:
        rows = pd.DataFrame(
            {
                'ticker': ['IN', 'BEFORE', 'LATE_LABEL', 'GAP', 'NO_LABEL'],
                'accepted_at': pd.to_datetime(
                    ['2026-07-01', '2026-05-01', '2026-11-01', '2026-07-02', '2026-07-03'], utc=True
                ),
                'label_known_at': pd.to_datetime(
                    ['2026-08-01', '2026-06-01', '2026-12-01', '2026-08-01', '2026-08-01'], utc=True
                ),
                'a': [1.0, 1.0, 1.0, np.nan, 1.0],
                'b': [2.0, 2.0, 2.0, 2.0, 2.0],
                'pct_change': [-0.1, 0.0, 0.0, 0.0, np.nan],
            }
        )
        assert holdout(rows, metadata())['ticker'].tolist() == ['IN', 'GAP']

    def test_scored_rows_keep_the_label_as_actual(self) -> None:
        rows = pd.DataFrame(
            {'ticker': ['X'], 'accepted_at': pd.to_datetime(['2026-07-01'], utc=True), 'pct_change': [-0.1]}
        )
        result = scored(rows, np.array([-0.07]), 'pct_change')
        assert result.to_dict('records') == [
            {'ticker': 'X', 'accepted_at': pd.Timestamp('2026-07-01', tz='UTC'), 'predicted': -0.07, 'actual': -0.1}
        ]


class TestThresholdTable:
    """Each threshold reports its shorts and their mean return, minus the actual return."""

    def test_table(self) -> None:
        rows = rows_scored(['2026-07-01'] * 4, [-0.12, -0.06, -0.01, 0.02], [-0.2, 0.1, -0.05, 0.3])
        table = threshold_table(rows, (0.0, -0.05, -0.10, -0.5))
        assert [(t.threshold, t.n_shorts, t.mean_return) for t in table] == [
            (0.0, 3, pytest.approx((0.2 - 0.1 + 0.05) / 3)),
            (-0.05, 2, pytest.approx((0.2 - 0.1) / 2)),
            (-0.10, 1, pytest.approx(0.2)),
            (-0.5, 0, None),
        ]


class TestCumulative:
    """Each short adds its return once by entry date; SPY is shorted over the same 30-day windows; both start at 0."""

    CLOSES = pd.Series(
        [100.0, 110.0, 90.0, 99.0],
        index=pd.to_datetime(['2026-07-01', '2026-07-06', '2026-07-31', '2026-08-04']),
    )

    def test_strategy_against_spy(self) -> None:
        shorts = rows_scored(
            ['2026-07-01T14:00Z', '2026-07-01T20:00Z', '2026-07-04T14:00Z'], [-0.1] * 3, [-0.1, -0.3, 0.1]
        )
        points = [(p.date, p.strategy, p.spy) for p in cumulative(shorts, self.CLOSES)]
        assert points == [
            (date(2026, 6, 30), 0.0, 0.0),
            # Two shorts on 1 July: +10% and +30%; SPY 100 -> 90 (31 July) gains 10% short, twice
            (date(2026, 7, 1), pytest.approx(0.4), pytest.approx(0.2)),
            # Saturday 4 July enters at Monday's 110 and exits at 99 on 4 August
            (date(2026, 7, 4), pytest.approx(0.3), pytest.approx(0.3)),
        ]

    def test_without_spy_prices_the_benchmark_is_null(self) -> None:
        shorts = rows_scored(['2026-07-01T14:00Z'], [-0.1], [-0.1])
        points = cumulative(shorts, pd.Series(dtype=float))
        assert [(p.date, p.strategy, p.spy) for p in points] == [
            (date(2026, 6, 30), 0.0, None),
            (date(2026, 7, 1), pytest.approx(0.1), None),
        ]

    def test_no_shorts_no_points(self) -> None:
        assert cumulative(rows_scored([], [], []), self.CLOSES) == ()


class TestImportance:
    """Permutation importance ranks the features that drive the predictions first, and keeps the top ones."""

    def test_the_driving_feature_ranks_first(self) -> None:
        rng = np.random.default_rng(0)
        X = rng.normal(size=(200, 3))
        y = 2 * X[:, 1] + 0.01 * X[:, 0]
        model = LinearRegression().fit(X, y)
        ranked = importance(model, X, y, ('noise', 'driver', 'tiny'), ImportanceSpec(3, 42, 100, 2))
        assert [item.feature for item in ranked] == ['driver', 'noise']
        assert ranked[0].importance > ranked[1].importance

    def test_no_rows_no_importance(self) -> None:
        assert importance(None, np.empty((0, 2)), np.empty(0), ('a', 'b'), ImportanceSpec(3, 42, 100, 2)) == ()


class TestMetadataFields:
    """Folds and the published version come from the saved metadata and file."""

    def test_folds_pair_the_model_and_zero_errors(self) -> None:
        result = folds(metadata(validation_mae=(0.15, 0.12), validation_zero_mae=(0.11, 0.10)))
        assert [(f.fold, f.model_mae, f.zero_mae) for f in result] == [(1, 0.15, 0.11), (2, 0.12, 0.10)]

    def test_models_before_walk_forward_have_no_folds(self) -> None:
        assert folds(metadata()) == ()

    def test_published_version(self) -> None:
        assert published_version(b'{"version": "20261120T210617Z"}') == '20261120T210617Z'
        assert published_version(None) is None


class TestModelReport:
    """The report combines the metadata with the scored holdout."""

    def test_report_json(self) -> None:
        rows = rows_scored(['2026-07-01T14:00Z', '2026-07-02T14:00Z'], [-0.08, 0.01], [-0.12, 0.02])
        report = json.loads(
            model_report(
                TRAINED,
                '20261120T210617Z',
                metadata(prediction_threshold=-0.05, report_thresholds=(0.0, -0.05)),
                rows,
                -0.05,
                pd.Series([100.0, 101.0, 95.0], index=pd.to_datetime(['2026-06-30', '2026-07-01', '2026-08-31'])),
                (),
            ).model_dump_json()
        )
        assert (report['version'], report['git_sha'], report['test_start']) == (
            '20261120T210617Z',
            '19a068a',
            '2026-06-01T00:00:00Z',
        )
        assert set(report['metrics']) == {'model', 'zero', 'train_mean'}
        assert report['cumulative'][0] == {'date': '2026-06-30', 'strategy': 0.0, 'spy': 0.0}
        assert (report['folds'], report['prediction_threshold']) == ([], -0.05)
        assert [row['threshold'] for row in report['thresholds']] == [0.0, -0.05]

    def test_models_without_report_thresholds_report_the_active_one(self) -> None:
        rows = rows_scored(['2026-07-01T14:00Z'], [-0.08], [-0.12])
        report = model_report(TRAINED, 'v1', metadata(), rows, -0.05, pd.Series(dtype=float), ())
        assert ([row.threshold for row in report.thresholds], report.prediction_threshold) == ([-0.05], -0.05)

    def test_shorts_are_strictly_below_the_threshold(self) -> None:
        rows = rows_scored(['2026-07-01'] * 3, [-0.08, -0.05, -0.051], [-0.1, -0.2, -0.3])
        report = model_report(TRAINED, 'v1', metadata(), rows, -0.05, pd.Series(dtype=float), ())
        assert (report.thresholds[0].n_shorts, report.thresholds[0].mean_return) == (2, pytest.approx(0.2))
        assert report.cumulative[-1].strategy == pytest.approx(0.4)


# explore.json

NOW = datetime(2026, 11, 23, 15, 0, tzinfo=UTC)


def transactions(*rows: tuple[str, str, str, str, bool, float, float, str]) -> pd.DataFrame:
    """Rows of (key, ticker, insider cik, acquired/disposed, derivative, shares, price, accepted_at)."""
    frame = pd.DataFrame(
        rows,
        columns=['key', 'ticker', 'insider_cik', 'acquired_disposed', 'derivative', 'shares', 'price', 'accepted_at'],
    )
    return frame.assign(insider_name=lambda f: 'Name ' + f['insider_cik'], owner_code='0100')


ROWS = transactions(
    ('k1', 'AAA', 'i1', 'D', False, 1000, 10.0, '2026-11-20T15:00:00Z'),
    ('k2', 'AAA', 'i2', 'D', False, 500, 10.0, '2026-11-21T15:00:00Z'),
    ('k3', 'BBB', 'i3', 'D', False, 100_000, 50.0, '2026-11-19T15:00:00Z'),
    ('k4', 'CCC', 'i4', 'A', False, 1_000_000, 50.0, '2026-11-19T15:00:00Z'),
    ('k5', 'DDD', 'i5', 'D', True, 1_000_000, 50.0, '2026-11-19T15:00:00Z'),
    # The same transaction in BT4 and BI4
    ('k1', 'AAA', 'i1', 'D', False, 1000, 10.0, '2026-11-20T15:00:00Z'),
)


class TestRole:
    """owner_code's bits name the insider's roles."""

    def test_roles(self) -> None:
        assert role('1100') == 'Director, Officer'
        assert role('0010') == '10% Owner'
        assert role('0000') is None
        assert role(None) is None


class TestSales:
    """Sales are non-derivative disposals, one per key, valued at shares times price."""

    def test_sales(self) -> None:
        sold = sales(ROWS)
        assert sorted(sold['key']) == ['k1', 'k2', 'k3']
        assert sold.set_index('key')['value'].to_dict() == {'k1': 10_000.0, 'k2': 5_000.0, 'k3': 5_000_000.0}

    def test_largest_first(self) -> None:
        top = largest_sales(sales(ROWS), 2)
        assert [(s.ticker, s.insider, s.role, s.shares, s.value) for s in top] == [
            ('BBB', 'Name i3', 'Officer', 100_000.0, 5_000_000.0),
            ('AAA', 'Name i1', 'Officer', 1000.0, 10_000.0),
        ]


class TestClusters:
    """A cluster is a ticker at least two distinct insiders sold."""

    def test_clusters(self) -> None:
        (cluster,) = clusters(sales(ROWS), 10)
        assert cluster.model_dump(mode='json') == {
            'ticker': 'AAA',
            'insiders': 2,
            'value': 15_000.0,
            'first_accepted_at': '2026-11-20T15:00:00Z',
            'last_accepted_at': '2026-11-21T15:00:00Z',
        }

    def test_more_insiders_first(self) -> None:
        rows = pd.concat(
            [
                ROWS,
                transactions(
                    *((f'b{i}', 'BBB', f'j{i}', 'D', False, 1, 1.0, '2026-11-19T16:00:00Z') for i in range(3))
                ),
            ]
        )
        assert [(c.ticker, c.insiders) for c in clusters(sales(rows), 10)] == [('BBB', 4), ('AAA', 2)]


class TestExploreReport:
    """The report serialises, and an empty window gives empty lists."""

    def test_report_json(self) -> None:
        report = json.loads(explore_report(NOW, ROWS, 10).model_dump_json())
        assert report['generated_at'] == '2026-11-23T15:00:00Z'
        assert report['largest_sales_7d'][0]['accepted_at'] == '2026-11-19T15:00:00Z'

    def test_empty_window(self) -> None:
        empty = transactions().astype({'derivative': bool})
        report = explore_report(NOW, empty, 10)
        assert (report.largest_sales_7d, report.clusters_7d) == ((), ())
