"""Trading simulation and prediction plotting utilities."""

import logging
from collections.abc import Mapping

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from sklearn.metrics import mean_absolute_error

logger = logging.getLogger(__name__)


def plot_predictions(
    y_test: pd.Series, y_pred_reg: pd.Series, financial_results: float, capital_invested: float, plot_path: str
) -> None:
    """Plot actual vs predicted negative returns and financial results.

    Args:
        y_test: Actual negative returns.
        y_pred_reg: Predicted negative returns.
        financial_results: Final capital resulting from the simulation.
        capital_invested: Total capital invested during the simulation.
        plot_path: File path the figure is saved to.
    """
    plt.figure(figsize=(10, 6))
    sns.scatterplot(x=y_test, y=y_pred_reg)
    plt.title('Actual vs Predicted Negative Returns (Filtered by Classifier)')
    plt.xlabel('Actual Negative Returns')
    plt.ylabel('Predicted Negative Returns')
    plt.axhline(0, color='red', linestyle='--', linewidth=1)  # Mark the zero line
    plt.axvline(0, color='red', linestyle='--', linewidth=1)
    plt.grid(True)

    # Financial result annotation
    plt.figtext(0.99, 0.01, f'Final Financial Result: ${financial_results:.2f}', ha='right', fontsize=12, color='green')
    plt.figtext(0.99, 0.10, f'Capital Invested: ${capital_invested:.2f}', ha='right', fontsize=12, color='green')

    # Save plot
    plt.savefig(plot_path, dpi=300)
    plt.close()
    logger.info(f'Plot saved as {plot_path}')


def run_simulation(
    real_returns: pd.Series, y_pred_negative: pd.Series, investment: float = 100, threshold: float = -0.3
) -> tuple[float, float, pd.Series, pd.Series]:
    """Short ``investment`` on every prediction at or below ``threshold`` and sum the outcome.

    Args:
        real_returns: Actual percentage-change returns aligned with the
            predictions.
        y_pred_negative: Predicted negative returns to evaluate.
        investment: Capital allocated to each short position. Defaults to 100.
        threshold: Predicted-return cutoff at or below which a position is
            taken. Defaults to -0.3.

    Returns:
        A tuple of the total capital invested, the accumulated gains, the
        actual returns, and the predicted negative returns.
    """
    # A short gains when the actual return is negative
    shorted = (y_pred_negative <= threshold).to_numpy()
    gains = -investment * real_returns[shorted].sum()
    return investment * shorted.sum(), gains, real_returns, y_pred_negative


def evaluate(y_true: pd.Series, predictions: Mapping[str, pd.Series]) -> dict[str, dict[str, float]]:
    """Score the model and its baselines on the same rows: error and the return of shorting their negatives.

    ``short_return`` is the mean return captured per short position (positive is a
    profit), taking every row each predictor expects to fall.

    Args:
        y_true: Actual returns.
        predictions: Predictor name -> predicted returns, aligned with ``y_true``.

    Returns:
        Predictor name -> ``mae``, ``short_return`` and ``n_shorts``.
    """
    return {
        name: {
            'mae': float(mean_absolute_error(y_true, pred)),
            'short_return': float(-y_true[shorted].mean()) if (shorted := pred < 0).any() else 0.0,
            'n_shorts': int(shorted.sum()),
        }
        for name, pred in predictions.items()
    }
