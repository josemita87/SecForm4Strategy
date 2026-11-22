"""Lakehouse reads analysed with pandas and scikit-learn, building model.json and explore.json.

model.json is the current model's training facts and its holdout replayed: thresholds, shorts, returns, importance.
The holdout is rebuilt from RT4 as training cut it: rows accepted from ``test_start`` on whose label was known
before the model was trained. It is scored with the saved pipeline on arrays in its ``features`` order.

explore.json is the largest insider sales of the last days, and the tickers several insiders sold together.
Sales are non-derivative transactions disposed of (``acquired_disposed == 'D'``), from BT4 (the batch load) and
BI4 (the live path), one row per transaction key. A sale's value is its shares times its reported price.
"""

import json
import logging
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any, NamedTuple

import numpy as np
import pandas as pd
from constants import DISPOSED, ROLES, Col, FeatureGroup, Section, file_name
from records import (
    Cluster,
    CumulativePoint,
    Explore,
    Fold,
    Importance,
    Metric,
    ModelReport,
    Sale,
    ThresholdRow,
)
from sklearn.inspection import permutation_importance

if TYPE_CHECKING:
    import numpy.typing as npt
    from secform4strategy_clients.ports import MarketData, ModelFiles, ModelStore, RecentRows, Site
    from secform4strategy_clients.records import ModelMetadata

logger = logging.getLogger(__name__)


# model.json

#: Benchmark the strategy's cumulative return is drawn against.
BENCHMARK = 'SPY'
#: Days a short is held, as the label's DELTA_PERIOD in target.
HOLDING_DAYS = 30


class ImportanceSpec(NamedTuple):
    """How permutation importance is computed: repeats, seed, the row sample and how many features are kept."""

    n_repeats: int
    random_state: int
    max_rows: int
    top: int


def published_version(content: bytes | None) -> str | None:
    """Return the model version a published model.json reports.

    Args:
        content: The published file, or None when there is none.

    Returns:
        Its version, or None.
    """
    return None if content is None else json.loads(content).get('version')


def holdout(rows: pd.DataFrame, metadata: ModelMetadata) -> pd.DataFrame:
    """Return the labelled holdout rows, sorted by acceptance: accepted from ``test_start``, labelled before training.

    Missing feature values stay, as in training's holdout score: the pipeline handles them.

    Args:
        rows: RT4 rows with the model's features, its target, ticker and timestamps.
        metadata: The model's metadata.

    Returns:
        The rows with a target value.
    """
    accepted = pd.to_datetime(rows[Col.ACCEPTED_AT], utc=True)
    known = pd.to_datetime(rows[Col.LABEL_KNOWN_AT], utc=True)
    held = rows[(accepted >= pd.Timestamp(metadata.test_start)) & (known < pd.Timestamp(metadata.trained_at))]
    return held.dropna(subset=[metadata.target]).sort_values(Col.ACCEPTED_AT, kind='stable')


def scored(rows: pd.DataFrame, predicted: npt.NDArray[np.float64], target: str) -> pd.DataFrame:
    """Return the holdout's ticker, acceptance, prediction and actual return.

    Args:
        rows: The holdout rows.
        predicted: The model's prediction per row.
        target: The label column.

    Returns:
        ``ticker``, ``accepted_at``, ``predicted`` and ``actual`` per row.
    """
    return pd.DataFrame(
        {
            Col.TICKER: rows[Col.TICKER].to_numpy(),
            Col.ACCEPTED_AT: pd.to_datetime(rows[Col.ACCEPTED_AT], utc=True).to_numpy(),
            Col.PREDICTED: predicted,
            Col.ACTUAL: rows[target].to_numpy(dtype=float),
        }
    )


def threshold_table(rows: pd.DataFrame, thresholds: Iterable[float]) -> tuple[ThresholdRow, ...]:
    """Return the shorts each threshold takes and their mean return (a short earns minus the actual return).

    Args:
        rows: Scored holdout rows.
        thresholds: Thresholds to short below.

    Returns:
        One row per threshold.
    """
    return tuple(
        ThresholdRow(
            threshold=threshold,
            n_shorts=len(shorted := rows.loc[rows[Col.PREDICTED] < threshold, Col.ACTUAL]),
            mean_return=float(-shorted.mean()) if len(shorted) else None,
        )
        for threshold in thresholds
    )


def cumulative(shorts: pd.DataFrame, closes: pd.Series[float]) -> tuple[CumulativePoint, ...]:
    """Return the summed return of the shorts by entry date, against shorting the benchmark over the same windows.

    Each short is one unit held ``HOLDING_DAYS``, so its return adds once; compounding overlapping trades would
    overstate them. The benchmark short of a trade runs from the first close on or after its acceptance date to
    the first close on or after ``HOLDING_DAYS`` later; it is null where those closes are missing. Both series
    start at 0 the day before the first entry.

    Args:
        shorts: Shorted rows with ``accepted_at`` and ``actual``.
        closes: Benchmark closes by date (naive midnight timestamps); empty when unavailable.

    Returns:
        The points, oldest first; empty without shorts.
    """
    if shorts.empty:
        return ()
    closes = closes.set_axis(pd.DatetimeIndex(closes.index)).sort_index()
    entered = pd.to_datetime(shorts[Col.ACCEPTED_AT], utc=True).dt.tz_localize(None).dt.normalize()

    def close_from(day: pd.Timestamp) -> float:
        """Return the first benchmark close on or after ``day``, NaN when there is none."""
        later = closes[closes.index >= day]
        return float(later.iloc[0]) if len(later) else np.nan

    benchmark = [1 - close_from(day + timedelta(days=HOLDING_DAYS)) / close_from(day) for day in entered]
    trades = pd.DataFrame(
        {Col.DAY: entered.dt.date.to_numpy(), Col.STRATEGY: (-shorts[Col.ACTUAL]).to_numpy(), Col.SPY: benchmark}
    )
    summed = trades.groupby(Col.DAY)[[Col.STRATEGY, Col.SPY]].sum(min_count=1).cumsum()
    first = summed.index[0] - timedelta(days=1)
    return (
        CumulativePoint(date=first, strategy=0.0, spy=0.0 if summed[Col.SPY].notna().any() else None),
        *(
            CumulativePoint(date=day, strategy=float(strategy), spy=None if np.isnan(spy) else float(spy))
            for day, strategy, spy in zip(summed.index, summed[Col.STRATEGY], summed[Col.SPY], strict=True)
        ),
    )


def importance(
    model: Any, X: npt.NDArray[Any], y: npt.NDArray[np.float64], features: Sequence[str], spec: ImportanceSpec
) -> tuple[Importance, ...]:
    """Return the features whose shuffling raises the holdout MAE most, on a seeded sample of rows.

    Args:
        model: The fitted pipeline, scoring arrays in ``features`` order.
        X: Holdout features.
        y: Holdout labels.
        features: Column names of ``X``.
        spec: Repeats, seed, sample size and how many features to keep.

    Returns:
        The top features, most important first.
    """
    if not len(X):
        return ()
    sample = np.random.default_rng(spec.random_state).permutation(len(X))[: spec.max_rows]
    result = permutation_importance(
        model,
        X[sample],
        y[sample],
        scoring='neg_mean_absolute_error',
        n_repeats=spec.n_repeats,
        random_state=spec.random_state,
    )
    ranked = sorted(zip(features, result.importances_mean, strict=True), key=lambda pair: -pair[1])
    return tuple(Importance(feature=name, importance=float(value)) for name, value in ranked[: spec.top])


def folds(metadata: ModelMetadata) -> tuple[Fold, ...]:
    """Return the walk-forward folds saved with the model; none for models trained before them.

    Args:
        metadata: The model's metadata.

    Returns:
        One fold per validation block, in time order.
    """
    return tuple(
        Fold(fold=fold, model_mae=model_mae, zero_mae=zero_mae)
        for fold, (model_mae, zero_mae) in enumerate(
            zip(metadata.validation_mae, metadata.validation_zero_mae, strict=True), start=1
        )
    )


def model_report(
    generated_at: datetime,
    version: str,
    metadata: ModelMetadata,
    rows: pd.DataFrame,
    shorts_below: float,
    closes: pd.Series[float],
    ranked: tuple[Importance, ...],
) -> ModelReport:
    """Return model.json from the metadata and the scored holdout.

    Args:
        generated_at: When the report is built.
        version: The model's version folder.
        metadata: The model's metadata.
        rows: Scored holdout rows.
        shorts_below: The threshold the model trades; the cumulative series shorts the rows predicted below it, and the
            threshold table falls back to it alone when the metadata names no report thresholds.
        closes: Benchmark closes by date.
        ranked: Permutation importance, most important first.

    Returns:
        The report.
    """
    return ModelReport(
        generated_at=generated_at,
        version=version,
        trained_at=metadata.trained_at,
        git_sha=metadata.git_sha[:7],
        params=dict(metadata.model_params),
        train_rows=metadata.train_rows,
        test_rows=metadata.test_rows,
        test_start=metadata.test_start,
        prediction_threshold=shorts_below,
        folds=folds(metadata),
        metrics={
            name: Metric(mae=scores.mae, short_return=scores.short_return, n_shorts=scores.n_shorts)
            for name, scores in metadata.metrics.items()
        },
        thresholds=threshold_table(rows, metadata.report_thresholds or (shorts_below,)),
        cumulative=cumulative(rows[rows[Col.PREDICTED] < shorts_below], closes),
        importance=ranked,
    )


class ModelSpec(NamedTuple):
    """How model.json is built: the threshold of models saved without one, and permutation importance."""

    fallback_threshold: float
    importance: ImportanceSpec


def benchmark_closes(market: MarketData, start: date) -> pd.Series[float]:
    """Return the benchmark's daily closes from ``start``; empty when the download failed, which the client logs.

    Args:
        market: Daily price history.
        start: The first date wanted.

    Returns:
        Adjusted closes by date.
    """
    bars = market.price_histories([BENCHMARK], {BENCHMARK: start})
    return pd.Series(bars[Col.CLOSE].to_numpy(dtype=float), index=pd.DatetimeIndex(bars[Col.DATE]).tz_localize(None))


def build_model(
    now: datetime,
    spec: ModelSpec,
    site: Site,
    models: ModelFiles,
    store: ModelStore,
    recent: RecentRows,
    market: MarketData,
) -> ModelReport | None:
    """Build model.json, unless the published one already reports the current model.

    Args:
        now: When the report is built.
        spec: How to build it.
        site: The site, whose model.json names the version published.
        models: The model store's pointer, read cheaply first.
        store: The model store the model is loaded from.
        recent: RT4 rows from the holdout's start.
        market: Benchmark prices.

    Returns:
        The report, or None when no model was saved or model.json is current.
    """
    if (version := models.latest_version()) is None or version == published_version(
        site.read(file_name(Section.MODEL))
    ):
        logger.info(f'model.json is current (model {version})')
        return None
    model, metadata = store.load()
    columns = dict.fromkeys([*metadata.features, metadata.target, Col.TICKER, Col.ACCEPTED_AT, Col.LABEL_KNOWN_AT])
    rows = holdout(recent.read_since(FeatureGroup.RT4, metadata.test_start, columns), metadata)
    X = rows[list(metadata.features)].to_numpy()
    y = rows[metadata.target].to_numpy(dtype=float)
    holdout_rows = scored(rows, model.predict(X) if len(X) else np.empty(0), metadata.target)
    logger.info(f'Model {version}: {len(holdout_rows)} complete holdout rows from {metadata.test_start}')
    return model_report(
        now,
        version,
        metadata,
        holdout_rows,
        spec.fallback_threshold if metadata.prediction_threshold is None else metadata.prediction_threshold,
        benchmark_closes(market, metadata.test_start.date() - timedelta(days=10)),
        importance(model, X, y, metadata.features, spec.importance),
    )


# explore.json

#: Columns explore.json reads.
COLUMNS = (
    Col.KEY,
    Col.TICKER,
    Col.INSIDER_CIK,
    Col.INSIDER_NAME,
    Col.OWNER_CODE,
    Col.ACQUIRED_DISPOSED,
    Col.DERIVATIVE,
    Col.SHARES,
    Col.PRICE,
    Col.ACCEPTED_AT,
)


def role(owner_code: object) -> str | None:
    """Return the insider's relationship to the issuer from ``owner_code``'s four bits.

    Args:
        owner_code: Director, officer, 10% owner and other, as ``'0100'``.

    Returns:
        The roles held, such as ``Director, Officer``, or None when the code is missing or empty.
    """
    if not isinstance(owner_code, str):
        return None
    return ', '.join(name for bit, name in zip(owner_code, ROLES, strict=False) if bit == '1') or None


def sales(rows: pd.DataFrame) -> pd.DataFrame:
    """Return the non-derivative disposals, one per key, with their value.

    Args:
        rows: Transactions from BT4 and BI4.

    Returns:
        The sales with a ``value`` column.
    """
    disposed = rows[(rows[Col.ACQUIRED_DISPOSED] == DISPOSED) & ~rows[Col.DERIVATIVE].fillna(False).astype(bool)]
    return disposed.drop_duplicates(Col.KEY, keep='last').assign(
        **{
            Col.ACCEPTED_AT: lambda frame: pd.to_datetime(frame[Col.ACCEPTED_AT], utc=True),
            Col.VALUE: lambda frame: (
                frame[Col.SHARES].astype(float).fillna(0) * frame[Col.PRICE].astype(float).fillna(0)
            ),
        }
    )


def largest_sales(sold: pd.DataFrame, limit: int) -> tuple[Sale, ...]:
    """Return the largest sales by value.

    Args:
        sold: Sales, as ``sales`` returns them.
        limit: How many.

    Returns:
        The sales, largest first.
    """
    return tuple(
        Sale(
            ticker=row[Col.TICKER],
            insider=row[Col.INSIDER_NAME] if pd.notna(row[Col.INSIDER_NAME]) else None,
            role=role(row[Col.OWNER_CODE]),
            shares=float(row[Col.SHARES]),
            value=float(row[Col.VALUE]),
            accepted_at=row[Col.ACCEPTED_AT],
        )
        for _, row in sold.nlargest(limit, Col.VALUE).iterrows()
    )


def clusters(sold: pd.DataFrame, limit: int) -> tuple[Cluster, ...]:
    """Return the tickers at least two distinct insiders sold, most insiders first, then by value.

    Args:
        sold: Sales, as ``sales`` returns them.
        limit: How many.

    Returns:
        The clusters.
    """
    grouped = (
        sold.groupby(Col.TICKER, observed=True)
        .agg(
            **{
                Col.INSIDERS: (Col.INSIDER_CIK, 'nunique'),
                Col.VALUE: (Col.VALUE, 'sum'),
                Col.FIRST_ACCEPTED_AT: (Col.ACCEPTED_AT, 'min'),
                Col.LAST_ACCEPTED_AT: (Col.ACCEPTED_AT, 'max'),
            }
        )
        .query(f'{Col.INSIDERS} >= 2')
        .sort_values([Col.INSIDERS, Col.VALUE], ascending=False)
        .head(limit)
    )
    return tuple(
        Cluster(
            ticker=str(ticker),
            insiders=int(row[Col.INSIDERS]),
            value=float(row[Col.VALUE]),
            first_accepted_at=row[Col.FIRST_ACCEPTED_AT],
            last_accepted_at=row[Col.LAST_ACCEPTED_AT],
        )
        for ticker, row in grouped.iterrows()
    )


def explore_report(generated_at: datetime, rows: pd.DataFrame, limit: int) -> Explore:
    """Return explore.json from the window's transactions.

    Args:
        generated_at: When the report is built.
        rows: Transactions of the window.
        limit: Length of each list.

    Returns:
        The report.
    """
    sold = sales(rows)
    return Explore(
        generated_at=generated_at, largest_sales_7d=largest_sales(sold, limit), clusters_7d=clusters(sold, limit)
    )


class ExploreSpec(NamedTuple):
    """The window explore.json covers, and the length of each list."""

    days: int
    limit: int


def read_window(recent: RecentRows, since: datetime, groups: Iterable[FeatureGroup]) -> pd.DataFrame:
    """Return the transactions accepted since ``since`` from each feature group, skipping those without rows.

    Args:
        recent: Feature-group rows from an event time on.
        since: The window's start.
        groups: The feature groups to read.

    Returns:
        Their rows, concatenated.
    """
    frames = [frame for group in groups if not (frame := recent.read_since(group, since, COLUMNS)).empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=list(COLUMNS))


def build_explore(now: datetime, spec: ExploreSpec, recent: RecentRows) -> Explore:
    """Read the window's transactions and build explore.json.

    Args:
        now: When the report is built.
        spec: The window and list length.
        recent: Feature-group rows from an event time on.

    Returns:
        The report.
    """
    rows = read_window(recent, now - timedelta(days=spec.days), (FeatureGroup.BT4, FeatureGroup.BI4))
    return explore_report(now, rows, spec.limit)
