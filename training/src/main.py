"""Entry point: wires the ports from config, tunes the regressor walk-forward, scores it on the holdout, saves it."""

import logging
import subprocess
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING

import model
import pandas as pd
import simulation
import sklearn
import validation
from config import LocalModels, S3Models, config
from constants import Col, FeatureGroup
from paths import SPEC_PATH
from pyiceberg.catalog import load_catalog
from secform4strategy_clients.feature_groups import load_feature_group_catalog
from secform4strategy_clients.feature_store import IcebergFeatureStore
from secform4strategy_clients.model_store import LocalModelStore
from secform4strategy_clients.ports import ModelMetadata
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

    # Grid search on arrays in `features` order (the order inference must follow); refit on all development rows
    params = {**config.model_params, 'random_state': config.random_state}
    search = GridSearchCV(
        model.build_pipeline(development[features], params),
        param_grid={f'regressor__{name}': values for name, values in config.param_grid.items()},
        cv=folds,
        scoring='neg_mean_absolute_error',
        n_jobs=-1,
        refit=True,
    )
    search.fit(development[features].to_numpy(), y_dev.to_numpy())
    pipeline = search.best_estimator_
    best_params = {name.removeprefix('regressor__'): value for name, value in search.best_params_.items()}
    logger.info(f'Best parameters: {best_params}')

    # The best model per fold against predicting no move, so its stability over time shows
    validation_mae = tuple(
        float(-search.cv_results_[f'split{fold}_test_score'][search.best_index_]) for fold in range(len(folds))
    )
    validation_zero_mae = tuple(float(y_dev.iloc[val].abs().mean()) for _, val in folds)
    for fold, (mae, zero_mae) in enumerate(zip(validation_mae, validation_zero_mae, strict=True), start=1):
        logger.info(f'fold {fold}: model MAE {mae:.5f}, zero MAE {zero_mae:.5f}')

    # The model must beat what needs no model: no move, the average move, and the ticker's own history
    y_pred = pd.Series(pipeline.predict(holdout[features].to_numpy()), index=holdout.index)
    metrics = simulation.evaluate(
        y_holdout,
        {
            'model': y_pred,
            'zero': pd.Series(0.0, index=holdout.index),
            'train_mean': pd.Series(y_dev.mean(), index=holdout.index),
            **(
                {'ticker_history': holdout[Col.AVG_TARGET_EXPANDING].fillna(y_dev.mean())}
                if Col.AVG_TARGET_EXPANDING in holdout
                else {}
            ),
        },
        config.prediction_threshold,
    )
    for name, scores in metrics.items():
        logger.info(f'{name}: {scores}')
    for threshold, taken in simulation.threshold_sweep(y_holdout, y_pred, config.report_thresholds).items():
        logger.info(f'threshold {threshold:+.2f}: {taken.n_shorts} shorts, mean return {taken.short_return:.2%}')

    shorted = y_pred < config.prediction_threshold
    simulation.plot_shorts(y_holdout[shorted], y_pred[shorted], metrics['model'], config.plot_path)

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
    model_store.save(
        pipeline,
        ModelMetadata(
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
            prediction_threshold=config.prediction_threshold,
            validation_mae=validation_mae,
            validation_zero_mae=validation_zero_mae,
        ),
    )
    logger.info(f'Model saved to: {config.model_store}')
