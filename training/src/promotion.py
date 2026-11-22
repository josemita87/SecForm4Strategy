"""The promotion gate: whether a saved model may become the one inference trades on (docs/training.md#promotion)."""

import math
from typing import TYPE_CHECKING, Literal, NamedTuple

if TYPE_CHECKING:
    from secform4strategy_clients.ports import ModelStore
    from secform4strategy_clients.records import ModelMetadata

#: ``never`` only saves the model; ``gated`` promotes it when every gate check passes.
Policy = Literal['never', 'gated']


class PromotionSpec(NamedTuple):
    """The gate's thresholds.

    Attributes:
        min_train_rows: The fewest development rows a model may train on.
        min_rows_ratio: The smallest share of the current model's development rows the new model may train on.
    """

    min_train_rows: int
    min_rows_ratio: float


class Check(NamedTuple):
    """One gate check's outcome."""

    name: str
    passed: bool
    detail: str


class Gate(NamedTuple):
    """The gate's verdict and the checks behind it."""

    promote: bool
    checks: tuple[Check, ...]


def gate(new: ModelMetadata, current: ModelMetadata | None, spec: PromotionSpec) -> Gate:
    """Check a new model's data, contract and holdout; the current model's scores are not compared, as holdouts differ.

    Only the current model's row count, feature-group version and features are read, so a current model saved
    before the classifier fields (a regressor) still compares.

    Args:
        new: The new model's metadata.
        current: The promoted model's metadata, or None when no model is promoted.
        spec: The thresholds.

    Returns:
        The verdict, which promotes only when every check passes.
    """
    scores = new.metrics.get('model')
    checks = (
        Check(
            'min_train_rows',
            new.train_rows >= spec.min_train_rows,
            f'{new.train_rows} train rows, at least {spec.min_train_rows} required',
        ),
        *(
            ()
            if current is None
            else (
                Check(
                    'train_rows_vs_current',
                    new.train_rows >= spec.min_rows_ratio * current.train_rows,
                    f'{new.train_rows} train rows, '
                    f'at least {spec.min_rows_ratio:.0%} of the current {current.train_rows}',
                ),
                Check(
                    'feature_group_version',
                    new.feature_group_version == current.feature_group_version,
                    f'version {new.feature_group_version}, current {current.feature_group_version}',
                ),
                Check(
                    'features',
                    new.features == current.features,
                    f'{len(new.features)} features, current {len(current.features)}; '
                    f'added {sorted(set(new.features) - set(current.features))}, '
                    f'removed {sorted(set(current.features) - set(new.features))}',
                ),
            )
        ),
        Check('test_rows', new.test_rows > 0, f'{new.test_rows} holdout rows'),
        Check(
            'holdout_average_precision',
            scores is not None and scores.average_precision is not None and math.isfinite(scores.average_precision),
            'no model scores' if scores is None else f'holdout average precision {scores.average_precision}',
        ),
        Check(
            'n_shorts',
            scores is not None and scores.n_shorts > 0,
            'no model scores' if scores is None else f'{scores.n_shorts} holdout shorts at the probability cutoff',
        ),
    )
    return Gate(all(check.passed for check in checks), checks)


def decide(
    store: ModelStore, version: str, metadata: ModelMetadata, policy: Policy, spec: PromotionSpec
) -> Gate | None:
    """Apply the policy to a saved version: promote it when the policy is ``gated`` and the gate passes.

    Args:
        store: The model store the version was saved to.
        version: The saved version.
        metadata: The saved version's metadata.
        policy: The promotion policy.
        spec: The gate's thresholds.

    Returns:
        The gate's verdict, or None under ``never``, which runs no gate.
    """
    if policy == 'never':
        return None
    current = None if (promoted := store.latest_version()) is None else store.metadata(promoted)
    if (verdict := gate(metadata, current, spec)).promote:
        store.promote(version)
    return verdict
