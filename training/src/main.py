"""Entry point that trains the AutoML model and runs the trading simulation."""

import logging
import os
from logging.handlers import RotatingFileHandler

import h2o
import pandas as pd
import sklearn
from config import config

# Modules
from feature_store import Connection

import training.src.models.automl as models
from training.src import helper, simulation

# Configure structured logging
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
    # Run the end-to-end training, prediction, simulation, and save pipeline.
    # Connect to the feature store
    feature_store = Connection()
    logger.info('Connected to the feature store')

    # Initialize AutoML model
    automl_model = models.AutoML(max_runtime_secs=config.max_runtime_secs, ratio=config.train_test_ratio)
    logger.info('Initialized AutoML model')

    # Load and preprocess data
    data: pd.DataFrame = helper.load_and_preprocess_data(
        feature_store=feature_store, columns_to_drop=config.columns_to_drop
    )

    logger.info(f'Data with shape: {data.shape}')
    logger.info('Loaded and preprocessed data')

    # Define features and target
    X = data.drop(columns=[config.target_feature])
    y = data[config.target_feature]

    # Define which columns the model should use for training
    features = [col for col in X.columns.tolist() if col not in config.identification_features]
    # Scale training features
    X_scaled: pd.DataFrame = helper.scale_features(data=X, features_to_scale=features)
    logger.debug(f'Scaled features: {X_scaled.head()}')

    # Split the data into train and test sets
    X_train, X_test, y_train, y_test = sklearn.model_selection.train_test_split(
        X_scaled, y, test_size=config.train_test_ratio, random_state=config.random_state
    )
    logger.info('Split data into train and test sets')

    # Initialize H2O and convert to H2OFrame
    train_data = h2o.H2OFrame(pd.concat([X_train, y_train], axis=1))
    test_data = h2o.H2OFrame(X_test)
    logger.info('Converted data to H2OFrame')

    # Train regressor using AutoML
    automl_model.train_regressor(train_data, target=config.target_feature, features=features)
    logger.info('Trained regressor using AutoML')

    # Run predictions on the test data using AutoML
    predictions: pd.DataFrame = automl_model.predict_shorts(data=test_data).set_index(X_test.index)
    logger.info('Generated predictions using AutoML')
    logger.info(f'Number of predictions: {test_data.shape[0]}')
    # Set index of predictions to match y_test
    y_pred = predictions['predict']

    # Extract predicted negative returns
    y_pred_negative = y_pred[y_pred < 0]

    # Common index of predicted negative returns and returns (y_test)
    common_index = y_pred_negative.index.intersection(y_test.index)

    # Align based on the common index
    real_returns = y_test.loc[common_index]

    # Run the financial simulation
    capital_invested, final_capital, y_negative, y_pred_negative = simulation.run_simulation(
        real_returns, y_pred_negative, threshold=0, investment=100
    )
    logger.info('Ran financial simulation')

    # Evaluate performance on negative returns
    mae_negative = sklearn.metrics.mean_absolute_error(y_negative, y_pred_negative)
    logger.info(f'MAE for negative returns: {mae_negative:.4f}')

    # Plot actual vs predicted negative returns
    simulation.plot_predictions(y_negative, y_pred_negative, final_capital, capital_invested)
    logger.info('Plotted predictions and financial results')

    # Save the best model
    os.makedirs(config.models_directory, exist_ok=True)
    h2o.save_model(model=automl_model.best_model, path=config.models_directory, force=True)
    logger.info(f'Model saved at: {config.models_directory}')

    logger.info('Training process completed')
