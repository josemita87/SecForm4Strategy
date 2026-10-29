"""Entry point: wires the ports from config, trains the return regressor, evaluates it and saves it."""

import logging
import subprocess
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import helper
import model
import pandas as pd
import simulation
import sklearn
from config import config
from pyiceberg.catalog import load_catalog
from secform4strategy_clients.feature_store import IcebergFeatureStore, load_feature_group_catalog
from secform4strategy_clients.model_store import LocalModelStore

# Configure structured logging (the handler needs the log directory to exist)
Path(config.log_path).parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(config.log_path, maxBytes=10 * 1024 * 1024, backupCount=5),
    ],
)
logger = logging.getLogger(__name__)


if __name__ == '__main__':
    feature_store = IcebergFeatureStore(
        load_catalog('default'),
        config.feature_store_namespace,
        load_feature_group_catalog(Path(__file__).parent / 'feature_groups.yaml', config.feature_group_version),
    )
    data = helper.load_and_preprocess_data(feature_store, config.columns_to_drop)
    logger.info(f'Loaded data with shape: {data.shape}')

    # Chronological split, purged: training keeps only labels already known when the test period starts
    data = data.sort_values('timestamp')
    test_start = data['timestamp'].iloc[int(len(data) * (1 - config.train_test_ratio))]
    train, test = data[data['label_known_at'] < test_start], data[data['timestamp'] >= test_start]
    features = [col for col in data.columns if col not in [config.target_feature, *config.identification_features]]
    X_train, y_train = train.drop(columns=[config.target_feature]), train[config.target_feature]
    X_test, y_test = test.drop(columns=[config.target_feature]), test[config.target_feature]
    logger.info(f'Train {len(train)} rows, test {len(test)} rows from {datetime.fromtimestamp(test_start / 1000, UTC)}')

    # Train on arrays in `features` order (the order inference must follow); preprocessing is fitted on train only
    params = {**config.model_params, 'random_state': config.random_state}
    pipeline = model.build_pipeline(X_train[features], params)
    pipeline.fit(X_train[features].to_numpy(), y_train.to_numpy())
    y_pred = pd.Series(pipeline.predict(X_test[features].to_numpy()), index=X_test.index)
    logger.info(f'Trained regressor; {len(y_pred)} test predictions')

    # The model must beat what needs no model: no move, the average move, and the ticker's own history
    metrics = simulation.evaluate(
        y_test,
        {
            'model': y_pred,
            'zero': pd.Series(0.0, index=y_test.index),
            'train_mean': pd.Series(y_train.mean(), index=y_test.index),
            **(
                {'ticker_history': X_test['avg_target_expanding'].fillna(y_train.mean())}
                if 'avg_target_expanding' in X_test
                else {}
            ),
        },
    )
    for name, scores in metrics.items():
        logger.info(f'{name}: {scores}')

    # Simulate shorting the predicted negative returns and plot them against the actual ones
    y_pred_negative = y_pred[y_pred < 0]
    capital_invested, final_capital, y_negative, y_pred_negative = simulation.run_simulation(
        y_test.loc[y_pred_negative.index], y_pred_negative, threshold=0, investment=100
    )
    simulation.plot_predictions(y_negative, y_pred_negative, final_capital, capital_invested, config.plot_path)

    # Save the fitted pipeline with the facts needed to reproduce and audit it
    try:
        git_sha = (
            config.git_sha
            or subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()
        )
    except OSError, subprocess.CalledProcessError:
        git_sha = 'unknown'
    LocalModelStore(config.model_path).save(
        pipeline,
        {
            'trained_at': datetime.now(UTC).isoformat(),
            'git_sha': git_sha,
            'sklearn_version': sklearn.__version__,
            'feature_group_version': config.feature_group_version,
            'target': config.target_feature,
            'features': features,
            'model_params': params,
            'train_rows': len(X_train),
            'test_rows': len(X_test),
            'test_start': datetime.fromtimestamp(test_start / 1000, UTC).isoformat(),
            'metrics': metrics,
            'simulation': {'capital_invested': capital_invested, 'gains': final_capital},
        },
    )
    logger.info(f'Model saved at: {config.model_path}')
