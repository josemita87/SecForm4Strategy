"""Model evaluation against baselines, and the plot of the shorts it would take."""

import logging
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import matplotlib.pyplot as plt
import seaborn as sns
from secform4strategy_clients.records import Scores
from sklearn.metrics import mean_absolute_error

if TYPE_CHECKING:
    import pandas as pd

logger = logging.getLogger(__name__)


def plot_shorts(y_true: pd.Series[float], y_pred: pd.Series[float], scores: Scores, plot_path: Path) -> None:
    """Plot actual against predicted returns for the rows the model would short, annotated with their outcome.

    Args:
        y_true: Actual returns of the shorted rows.
        y_pred: Predicted returns of the shorted rows.
        scores: The model's ``evaluate`` scores.
        plot_path: File path the figure is saved to.
    """
    plt.figure(figsize=(10, 6))
    sns.scatterplot(x=y_true, y=y_pred)
    plt.title('Actual vs predicted returns of the shorted rows')
    plt.xlabel('Actual return')
    plt.ylabel('Predicted return')
    plt.axhline(0, color='red', linestyle='--', linewidth=1)
    plt.axvline(0, color='red', linestyle='--', linewidth=1)
    plt.grid(True)
    plt.figtext(
        0.99,
        0.01,
        f'{scores.n_shorts} shorts, mean return captured {scores.short_return:.2%}',
        ha='right',
        fontsize=12,
    )
    plt.savefig(plot_path, dpi=300)
    plt.close()
    logger.info(f'Plot saved as {plot_path}')


class Shorts(NamedTuple):
    """The mean return captured per short (positive is a profit), and how many rows were shorted."""

    short_return: float
    n_shorts: int


def shorts(y_true: pd.Series[float], pred: pd.Series[float], threshold: float) -> Shorts:
    """Short every row predicted to return less than ``threshold``.

    Args:
        y_true: Actual returns.
        pred: Predicted returns, aligned with ``y_true``.
        threshold: Rows predicted below it are shorted.

    Returns:
        The return captured per short (0.0 when none) and the number of shorts.
    """
    shorted = pred < threshold
    return Shorts(float(-y_true[shorted].mean()) if shorted.any() else 0.0, int(shorted.sum()))


def evaluate(
    y_true: pd.Series[float], predictions: Mapping[str, pd.Series[float]], threshold: float
) -> dict[str, Scores]:
    """Score the model and its baselines on the same rows: error and the return of the shorts they would take.

    Args:
        y_true: Actual returns.
        predictions: Predictor name -> predicted returns, aligned with ``y_true``.
        threshold: Each predictor shorts the rows it predicts below this return.

    Returns:
        Predictor name -> its scores.
    """
    return {
        name: Scores(float(mean_absolute_error(y_true, pred)), *shorts(y_true, pred, threshold))
        for name, pred in predictions.items()
    }


def threshold_sweep(
    y_true: pd.Series[float], pred: pd.Series[float], thresholds: Iterable[float]
) -> dict[float, Shorts]:
    """Return the shorts taken at each threshold, to show how the captured return trades off against their number.

    Args:
        y_true: Actual returns.
        pred: Predicted returns, aligned with ``y_true``.
        thresholds: Thresholds to short below.

    Returns:
        Threshold -> the shorts taken below it.
    """
    return {threshold: shorts(y_true, pred, threshold) for threshold in thresholds}
