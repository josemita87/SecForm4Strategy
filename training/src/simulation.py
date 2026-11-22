"""Model evaluation: the probability cutoff, the holdout scores against baselines, and the plot of the shorts taken.

A row is shorted when its predicted probability of being a short winner is at least the cutoff. A short captures
minus the row's actual return, so a positive captured return is a profit.
"""

import logging
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from constants import TRIMMED_SHARE, Col
from secform4strategy_clients.records import CutoffShorts, Scores
from sklearn.metrics import average_precision_score, roc_auc_score

if TYPE_CHECKING:
    import numpy.typing as npt

logger = logging.getLogger(__name__)


def trimmed_mean(values: npt.NDArray[np.float64], share: float) -> float:
    """Return the mean after dropping ``share`` of the values from each end, as ``scipy.stats.trim_mean`` does.

    Args:
        values: A non-empty array.
        share: The share cut from each end.

    Returns:
        The trimmed mean.
    """
    cut = int(share * len(values))
    return float(np.sort(values)[cut : len(values) - cut].mean())


def cutoff_shorts(returns: npt.NDArray[np.float64], proba: npt.NDArray[np.float64], cutoff: float) -> CutoffShorts:
    """Short every row whose probability is at least ``cutoff``.

    Args:
        returns: Actual returns.
        proba: Predicted probabilities of a short winner, aligned with ``returns``.
        cutoff: The probability cutoff.

    Returns:
        The number of shorts and their mean and median captured return, None without shorts.
    """
    captured = -returns[proba >= cutoff]
    return CutoffShorts(
        cutoff,
        len(captured),
        float(captured.mean()) if len(captured) else None,
        float(np.median(captured)) if len(captured) else None,
    )


def cutoff_sweep(
    returns: npt.NDArray[np.float64], proba: npt.NDArray[np.float64], cutoffs: Iterable[float]
) -> tuple[CutoffShorts, ...]:
    """Return the shorts taken at each cutoff, to show how the captured return trades off against their number.

    Args:
        returns: Actual returns.
        proba: Predicted probabilities, aligned with ``returns``.
        cutoffs: Cutoffs to short at.

    Returns:
        One entry per cutoff, in the given order.
    """
    return tuple(cutoff_shorts(returns, proba, cutoff) for cutoff in cutoffs)


class FoldCutoff(NamedTuple):
    """One candidate cutoff on the development folds.

    Attributes:
        cutoff: The probability cutoff.
        n_shorts: Shorts over every fold's validation rows.
        fewest_shorts: Shorts of the fold with the fewest.
        median_return: Median captured return over every fold's shorts; None when a fold has too few shorts.
    """

    cutoff: float
    n_shorts: int
    fewest_shorts: int
    median_return: float | None


def fold_cutoffs(
    fold_returns: Sequence[npt.NDArray[np.float64]],
    fold_proba: Sequence[npt.NDArray[np.float64]],
    cutoffs: Iterable[float],
    min_shorts: int,
) -> tuple[FoldCutoff, ...]:
    """Score each cutoff on the folds' validation predictions by the median captured return of their shorts.

    The median keeps a few extreme returns from deciding the cutoff. A cutoff whose shorts in any fold are
    fewer than ``min_shorts`` gets no median, so it cannot be chosen.

    Args:
        fold_returns: Each fold's actual validation returns.
        fold_proba: Each fold's predicted validation probabilities, aligned with ``fold_returns``.
        cutoffs: Candidate cutoffs.
        min_shorts: The fewest shorts a fold may take at a qualifying cutoff.

    Returns:
        One entry per cutoff, in the given order.
    """
    candidates = []
    for cutoff in cutoffs:
        captured = [-returns[proba >= cutoff] for returns, proba in zip(fold_returns, fold_proba, strict=True)]
        fewest = min(len(fold) for fold in captured)
        pooled = np.concatenate(captured)
        candidates.append(
            FoldCutoff(cutoff, len(pooled), fewest, float(np.median(pooled)) if fewest >= min_shorts else None)
        )
    return tuple(candidates)


def choose_cutoff(candidates: Iterable[FoldCutoff]) -> float | None:
    """Return the qualifying cutoff with the highest median captured return; the lowest cutoff wins a tie.

    Args:
        candidates: Scored cutoffs, as ``fold_cutoffs`` returns them.

    Returns:
        The cutoff, or None when none qualifies.
    """
    qualifying = [candidate for candidate in candidates if candidate.median_return is not None]
    return max(qualifying, key=lambda c: (c.median_return, -c.cutoff)).cutoff if qualifying else None


def score(
    returns: npt.NDArray[np.float64], winners: npt.NDArray[np.bool_], proba: npt.NDArray[np.float64], cutoff: float
) -> Scores:
    """Score one predictor: its ranking of short winners, and the shorts it takes at ``cutoff``.

    Args:
        returns: Actual returns.
        winners: Whether each row is a short winner.
        proba: Predicted probabilities, aligned with ``returns``.
        cutoff: The probability cutoff.

    Returns:
        The scores; average precision and ROC-AUC are None when the rows hold one class only.
    """
    shorted = proba >= cutoff
    captured = -returns[shorted]
    both_classes = 0 < winners.sum() < len(winners)
    return Scores(
        short_return=float(captured.mean()) if len(captured) else 0.0,
        n_shorts=len(captured),
        average_precision=float(average_precision_score(winners, proba)) if both_classes else None,
        roc_auc=float(roc_auc_score(winners, proba)) if both_classes else None,
        precision=float(winners[shorted].mean()) if len(captured) else None,
        median_return=float(np.median(captured)) if len(captured) else None,
        trimmed_return=trimmed_mean(captured, TRIMMED_SHARE) if len(captured) else None,
    )


def evaluate(
    returns: pd.Series[float], label_return: float, predictions: Mapping[str, pd.Series[float]], cutoff: float
) -> dict[str, Scores]:
    """Score the model and its baselines on the same rows.

    Args:
        returns: Actual returns.
        label_return: A row is a short winner when its return is below this.
        predictions: Predictor name -> predicted probabilities, aligned with ``returns``.
        cutoff: Each predictor shorts the rows at or above this probability.

    Returns:
        Predictor name -> its scores.
    """
    actual = np.asarray(returns, dtype=np.float64)
    return {
        name: score(actual, actual < label_return, np.asarray(proba, dtype=np.float64), cutoff)
        for name, proba in predictions.items()
    }


def ticker_history(labels: pd.DataFrame, rows: pd.DataFrame, target: str, label_return: float) -> pd.Series[float]:
    """Return, per row, the share of short winners among its ticker's labels known before its acceptance.

    The rule is target's ``avg_target_expanding`` on the binary label: a label counts once its
    ``label_known_at`` is strictly before the row's ``accepted_at``.

    Args:
        labels: Labelled rows with ``ticker``, ``label_known_at`` and ``target``.
        rows: Rows with ``ticker`` and ``accepted_at``, uniquely indexed.
        target: The return column.
        label_return: A label is a short winner when its return is below this.

    Returns:
        The share per row, indexed like ``rows``; NaN where no label of its ticker was known yet.
    """
    known = labels.dropna(subset=[target]).sort_values(Col.LABEL_KNOWN_AT)
    tickers: pd.Series[str] = known[Col.TICKER].astype(str)
    winners = (known[target] < label_return).astype(float).groupby(tickers, observed=True)
    columns: dict[str, pd.Series] = {
        Col.TICKER_HISTORY: winners.cumsum() / (winners.cumcount() + 1),
        Col.TICKER: tickers,
    }
    known = known.assign(**columns)
    ordered = rows.sort_values(Col.ACCEPTED_AT)
    shares: pd.Series[float] = (
        pd.merge_asof(
            ordered[[Col.ACCEPTED_AT]].assign(**{Col.TICKER: ordered[Col.TICKER].astype(str)}),
            known[[Col.TICKER, Col.LABEL_KNOWN_AT, Col.TICKER_HISTORY]],
            left_on=Col.ACCEPTED_AT,
            right_on=Col.LABEL_KNOWN_AT,
            by=Col.TICKER,
            direction='backward',
            allow_exact_matches=False,
        )[Col.TICKER_HISTORY]
        .set_axis(ordered.index)
        .reindex(rows.index)
    )
    return shares


def _pct(value: float | None) -> str:
    """Format a return as a signed percentage, ``n/a`` when undefined."""
    return 'n/a' if value is None else f'{value:+.2%}'


def _ratio(value: float | None) -> str:
    """Format a score in [0, 1] to four decimals, ``n/a`` when undefined."""
    return 'n/a' if value is None else f'{value:.4f}'


def describe(name: str, scores: Scores, cutoff: float) -> str:
    """Return one log line of a predictor's holdout scores.

    Args:
        name: The predictor.
        scores: Its scores.
        cutoff: The cutoff its shorts were taken at.

    Returns:
        A line such as ``model: AP 0.3100, ROC-AUC 0.5800, precision 0.3600, 812 shorts at 0.55, ...``.
    """
    return (
        f'{name}: AP {_ratio(scores.average_precision)}, ROC-AUC {_ratio(scores.roc_auc)}, '
        f'precision {_ratio(scores.precision)}, {scores.n_shorts} shorts at {cutoff:.2f}, '
        f'mean return {_pct(scores.short_return if scores.n_shorts else None)}, '
        f'median return {_pct(scores.median_return)}, trimmed mean return {_pct(scores.trimmed_return)}'
    )


def describe_cutoff(prefix: str, shorts: CutoffShorts) -> str:
    """Return one log line of the shorts taken at a cutoff.

    Args:
        prefix: What the shorts were scored on, such as ``holdout``.
        shorts: The shorts.

    Returns:
        A line such as ``holdout cutoff 0.55: 812 shorts, mean return +1.20%, median return +2.10%``.
    """
    return (
        f'{prefix} cutoff {shorts.cutoff:.2f}: {shorts.n_shorts} shorts, '
        f'mean return {_pct(shorts.mean_return)}, median return {_pct(shorts.median_return)}'
    )


def plot_shorts(
    returns: pd.Series[float], proba: pd.Series[float], cutoff: float, scores: Scores, plot_path: Path
) -> None:
    """Plot the predicted probability against the actual return of every holdout row, marking the cutoff.

    Args:
        returns: Actual holdout returns.
        proba: Predicted probabilities, aligned with ``returns``.
        cutoff: The probability cutoff; rows on or above it are shorted.
        scores: The model's ``evaluate`` scores.
        plot_path: File path the figure is saved to.
    """
    plt.figure(figsize=(10, 6))
    sns.scatterplot(x=returns, y=proba, hue=proba >= cutoff, s=8, alpha=0.4, legend=False)
    plt.title('Predicted probability of a short winner against the actual return, holdout')
    plt.xlabel('Actual return')
    plt.ylabel('Predicted probability')
    plt.axhline(cutoff, color='red', linestyle='--', linewidth=1)
    plt.axvline(0, color='grey', linestyle='--', linewidth=1)
    plt.grid(True)
    plt.figtext(
        0.99,
        0.01,
        f'{scores.n_shorts} shorts at {cutoff:.2f}, median return captured {_pct(scores.median_return)}',
        ha='right',
        fontsize=12,
    )
    plt.savefig(plot_path, dpi=300)
    plt.close()
    logger.info(f'Plot saved as {plot_path}')
