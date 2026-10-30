"""Model evaluation against baselines, and the plot of the shorts it would take."""

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

import matplotlib.pyplot as plt
import seaborn as sns
from secform4strategy_clients.ports import Scores
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


def evaluate(y_true: pd.Series[float], predictions: Mapping[str, pd.Series[float]]) -> dict[str, Scores]:
    """Score the model and its baselines on the same rows: error and the return of shorting their negatives.

    Each predictor shorts every row it expects to fall.

    Args:
        y_true: Actual returns.
        predictions: Predictor name -> predicted returns, aligned with ``y_true``.

    Returns:
        Predictor name -> its scores.
    """
    return {
        name: Scores(
            mae=float(mean_absolute_error(y_true, pred)),
            short_return=float(-y_true[shorted].mean()) if (shorted := pred < 0).any() else 0.0,
            n_shorts=int(shorted.sum()),
        )
        for name, pred in predictions.items()
    }
