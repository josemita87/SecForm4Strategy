import h2o
from h2o.automl import H2OAutoML
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error
from models.automl import AutoML  
from preprocessing import load_and_preprocess_data, scale_features
from simulation import run_simulation, plot_predictions
from loguru import logger
import pandas as pd
import os

def main():

    #Initialize AutoML model (init method initializes H2O cluster)
    automl_model = AutoML(max_runtime_secs=180)
    file_path = '/app/src/data_sources/final_df.csv'
    
    # Load and preprocess data
    data:pd.DataFrame = load_and_preprocess_data(file_path)

    # Define features and target
    X = data.drop(columns=['pct_change'])
    y = data['pct_change']

    # Scale training features
    X_scaled:pd.DataFrame = scale_features(X)
    
    logger.debug(X_scaled)
    # Split the data into train and test sets
    X_train, X_test, y_train, y_test = train_test_split(X_scaled, y, test_size=0.3, random_state=42)
    
    # Initialize H2O and convert to H2OFrame
    train_data = h2o.H2OFrame(pd.concat([X_train, y_train], axis=1))
    test_data = h2o.H2OFrame(X_test)
    
    # Define the target and features for H2O AutoML
    target_regressor = 'pct_change'
    #target_classifier = 'negative_returns'

    features = X_train.columns.tolist()
  
    #Train the classifier using AutoML
    #automl_model.train_classifier(train_data, target=target_classifier, features=features)

    # Train regressor using AutoML
    automl_model.train_regressor(train_data, target=target_regressor, features=features)
    
    #classification:pd.DataFrame = automl_model.classify(test_data)

    # Run predictions on the test data using AutoML
    predictions:pd.DataFrame = automl_model.predict_shorts(test_data).set_index(X_test.index)

    # Set index of predictions to match y_test
    y_pred = predictions['predict']
    
    # Extract predicted negative returns
    y_pred_negative = y_pred[y_pred < 0]

    # Find common index between predicted negative returns and returns (y_test)
    common_index = y_pred_negative.index.intersection(y_test.index)
   
    # Align y_negative with y_pred_negative based on the common index
    real_returns = y_test.loc[common_index]


    # Run the financial simulation
    capital_invested, final_capital, y_negative, y_pred_negative = run_simulation(
        real_returns, 
        y_pred_negative, 
        threshold=0,
        investment=100
    )
    
    # Evaluate performance on negative returns
    mae_negative = mean_absolute_error(y_negative, y_pred_negative)
    logger.info(f"MAE for negative returns: {mae_negative:.4f}")
    
    # Plot actual vs predicted negative returns and financial result
    plot_predictions(y_negative, y_pred_negative, final_capital, capital_invested)

if __name__ == "__main__":
    main()