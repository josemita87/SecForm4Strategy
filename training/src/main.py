"""Entry point: wires the ports, tunes the classifier walk-forward, picks its cutoff, scores, saves and gates it."""

import logging
import subprocess
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING

import model
import pandas as pd
import promotion
import simulation
import sklearn
import validation
from config import LocalModels, S3Models, config
from constants import FeatureGroup
from paths import SPEC_PATH
from pyiceberg.catalog import load_catalog
from secform4strategy_clients.feature_groups import load_feature_group_catalog
from secform4strategy_clients.feature_store import IcebergFeatureStore
from secform4strategy_clients.model_store import LocalModelStore
from secform4strategy_clients.records import ModelMetadata
from secform4strategy_clients.s3_model_store import S3ModelStore
from sklearn.model_selection import GridSearchCV

if TYPE_CHECKING:
    from secform4strategy_clients.ports import FeatureStore, ModelStore

# Configure structured logging (the handler needs the log directory to exist)
config.log_path.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(config.log_path, maxBytes=10 * 1024 * 1024, backupCount=5),
    ],
)
logger = logging.getLogger(__name__)


if __name__ == '__main__':
    feature_store: FeatureStore = IcebergFeatureStore(
        load_catalog('default'),
        config.feature_store_namespace,
        load_feature_group_catalog(SPEC_PATH, config.feature_group_version),
    )
    # pandas reads a tuple as one label, so the columns go as a list
    data = feature_store.read(FeatureGroup.RT4).drop(columns=list(config.columns_to_drop), errors='ignore')
    logger.info(f'Loaded data with shape: {data.shape}')

    # Purged holdout at the end, scored once; walk-forward folds over the development rows before it
    development, holdout, holdout_start = validation.holdout_split(data, config.holdout_ratio)
    folds = validation.walk_forward_folds(development, config.n_folds)
    for line in validation.describe_folds(development, folds):
        logger.info(line)
    logger.info(f'Development {len(development)} rows, holdout {len(holdout)} rows from {holdout_start}')
    features = [col for col in data.columns if col not in (config.target_feature, *config.identification_features)]
    y_dev, y_holdout = development[config.target_feature], holdout[config.target_feature]
    # The label: a short winner falls below SHORT_RETURN over the horizon
    winners_dev = (y_dev < config.short_return).to_numpy()
    X_dev = development[features].to_numpy()

    # Grid search on arrays in `features` order (the order inference must follow); refit on all development rows
    params = {**config.model_params, 'random_state': config.random_state}
    search = GridSearchCV(
        unfitted := model.build_pipeline(development[features], params),
        param_grid={f'classifier__{name}': values for name, values in config.param_grid.items()},
        cv=folds,
        scoring='average_precision',
        n_jobs=-1,
        refit=True,
    )
    search.fit(X_dev, winners_dev)
    pipeline = search.best_estimator_
    best_params = {name.removeprefix('classifier__'): value for name, value in search.best_params_.items()}
    logger.info(f'Best parameters: {best_params}')

    # The best model per fold against a constant predictor, whose average precision is the fold's base rate
    validation_ap = tuple(
        float(search.cv_results_[f'split{fold}_test_score'][search.best_index_]) for fold in range(len(folds))
    )
    validation_base_rate = tuple(float(winners_dev[val].mean()) for _, val in folds)
    for fold, (ap, base_rate) in enumerate(zip(validation_ap, validation_base_rate, strict=True), start=1):
        logger.info(f'fold {fold}: model AP {ap:.4f}, base rate {base_rate:.4f}')

    # The cutoff comes from the folds' validation predictions only; the holdout decides nothing
    candidates = simulation.fold_cutoffs(
        [y_dev.iloc[val].to_numpy(dtype=float) for _, val in folds],
        model.fold_probabilities(unfitted, search.best_params_, X_dev, winners_dev, folds),
        config.cutoff_grid,
        config.min_fold_shorts,
    )
    for candidate in candidates:
        logger.info(
            f'folds cutoff {candidate.cutoff:.2f}: {candidate.n_shorts} shorts, fewest in a fold '
            f'{candidate.fewest_shorts}, median return '
            + ('n/a (too few shorts)' if candidate.median_return is None else f'{candidate.median_return:+.2%}')
        )
    if (cutoff := simulation.choose_cutoff(candidates)) is None:
        cutoff = config.fallback_cutoff
        logger.warning(f'No cutoff takes {config.min_fold_shorts} shorts in every fold; falling back to {cutoff:.2f}')
    logger.info(f'Probability cutoff: {cutoff:.2f}')

    # The model must beat what needs no model: the base rate, and the ticker's own history of short winners
    proba = pd.Series(pipeline.predict_proba(holdout[features].to_numpy())[:, 1], index=holdout.index)
    base_rate = float((y_holdout < config.short_return).mean())
    logger.info(f'Holdout base rate (share of short winners, AP of a constant): {base_rate:.4f}')
    metrics = simulation.evaluate(
        y_holdout,
        config.short_return,
        {
            'model': proba,
            'base_rate': pd.Series(winners_dev.mean(), index=holdout.index),
            'ticker_history': simulation.ticker_history(
                data, holdout, config.target_feature, config.short_return
            ).fillna(winners_dev.mean()),
        },
        cutoff,
    )
    for name, scores in metrics.items():
        logger.info(simulation.describe(name, scores, cutoff))
    sweep = simulation.cutoff_sweep(y_holdout.to_numpy(dtype=float), proba.to_numpy(), config.cutoff_grid)
    for shorts in sweep:
        logger.info(simulation.describe_cutoff('holdout', shorts))

    simulation.plot_shorts(y_holdout, proba, cutoff, metrics['model'], config.plot_path)

    # Save the fitted pipeline with the facts needed to reproduce and audit it
    try:
        git_sha = (
            config.git_sha
            or subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()
        )
    except OSError, subprocess.CalledProcessError:
        git_sha = 'unknown'
    model_store: ModelStore
    match config.model_store:
        case LocalModels() as local:
            model_store = LocalModelStore(local.path)
        case S3Models() as s3:
            model_store = S3ModelStore(s3.region, s3.bucket, s3.prefix)
    version = model_store.save(
        pipeline,
        metadata := ModelMetadata(
            trained_at=datetime.now(UTC),
            git_sha=git_sha,
            sklearn_version=sklearn.__version__,
            feature_group_version=config.feature_group_version,
            target=config.target_feature,
            features=tuple(features),
            model_params={**params, **best_params},
            train_rows=len(development),
            test_rows=len(holdout),
            test_start=holdout_start.to_pydatetime(),
            metrics=metrics,
            probability_cutoff=cutoff,
            label_return=config.short_return,
            base_rate=base_rate,
            validation_ap=validation_ap,
            validation_base_rate=validation_base_rate,
            cutoff_sweep=sweep,
        ),
    )
    logger.info(f'Model {version} saved to: {config.model_store}')

    # Promotion: inference loads only the promoted version, never a merely saved one
    verdict = promotion.decide(
        model_store,
        version,
        metadata,
        config.promote,
        promotion.PromotionSpec(config.min_train_rows, config.min_rows_ratio),
    )
    if verdict is None:
        logger.info(
            f'Model {version} saved, not promoted (PROMOTE=never); promote it with scripts/promote_model.sh {version}'
        )
    else:
        for check in verdict.checks:
            logger.info(f'gate {check.name}: {"pass" if check.passed else "FAIL"} ({check.detail})')
        logger.log(
            logging.INFO if verdict.promote else logging.WARNING,
            f'Model {version} {"promoted" if verdict.promote else "not promoted: a gate check failed"}',
        )
