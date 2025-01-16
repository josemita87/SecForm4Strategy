import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.metrics import mean_absolute_error
from sklearn.preprocessing import StandardScaler
import loguru
import matplotlib.pyplot as plt
import seaborn as sns
from src.models import automl


def load_and_preprocess_data(file_path: str):
    """Load data and prepare it for training."""
    data = pd.read_csv(file_path)
    
    # Drop unnecessary columns
    columns_to_drop = ['ticker', 'company_cik', 'key', 'timestamp', 'date', 'coding', 'price']
    data = data.drop(columns=columns_to_drop)
    
    loguru.logger.info(f"Data loaded and processed with shape: {data.shape}")
    return data

def train_classifier(X, y):
    """Train a classifier to predict if the return will be negative."""
    classifier = RandomForestClassifier(random_state=42)
    classifier.fit(X, y < 0)  # Binary target: 1 if negative return, 0 otherwise
    return classifier

def train_regressor(X, y):
    """Train a regressor to predict the magnitude of negative returns."""
    regressor = RandomForestRegressor(random_state=42)
    regressor.fit(X, y)  # Train on the full dataset (regression task)
    return regressor

def plot_predictions(y_test, y_pred_reg, financial_results, capital_invested):
    """Plot actual vs predicted negative returns and financial results."""
    plt.figure(figsize=(10, 6))
    sns.scatterplot(x=y_test, y=y_pred_reg)
    plt.title('Actual vs Predicted Negative Returns (Filtered by Classifier)')
    plt.xlabel('Actual Negative Returns')
    plt.ylabel('Predicted Negative Returns')
    plt.axhline(0, color='red', linestyle='--', linewidth=1)  # Mark the zero line
    plt.axvline(0, color='red', linestyle='--', linewidth=1)
    plt.grid(True)
    
    # Financial result annotation
    plt.figtext(0.99, 0.01, f"Final Financial Result: ${financial_results:.2f}", ha='right', fontsize=12, color='green')
    plt.figtext(0.99, 0.10, f"Final Financial Result: ${capital_invested:.2f}", ha='right', fontsize=12, color='green')
    
    # Save plot
    plt.savefig('/app/src/negative_returns_plot_with_financial.png')
    plt.close()
    loguru.logger.info("Plot saved as /app/src/negative_returns_plot_with_financial.png")

def run_simulation(X, y, classifier, regressor, investment=100, threshold=-0.25):
    """Run a simulation based on the model's predictions."""
    # Predict negative returns
    y_pred_class = classifier.predict(X)
    
    # Identify the points where the classifier predicts a decrease of more than 20%
    X_negative = X[y_pred_class == 1]  # Classifier predicts 1 for negative returns
    y_negative = y[y_pred_class == 1]  # Corresponding actual values
    y_pred_negative = regressor.predict(X_negative)  # Predicted magnitude of the negative returns
    #breakpoint()
    # Simulate the investment
    gains = 0
    capital_invested = investment
    for i, predicted_change in enumerate(y_pred_negative):
        if predicted_change <= threshold:  # If the model predicts a decrease > 20%
            gains += ((-investment)*y_negative.iloc[i])  # Use the actual pct_change (not predicted) to update capital
            capital_invested += investment  # Update capital invested
            loguru.logger.debug(gains)
    return capital_invested, gains, y_negative, y_pred_negative

def main():
    file_path = '/app/src/final_df.csv'
    
    # Load and preprocess data
    data = load_and_preprocess_data(file_path)
    
    # Define features and target
    X = data.drop(columns=['pct_change'])
    y = data['pct_change']
    
    # Scale features
    scaler = StandardScaler()
    X_scaled = pd.DataFrame(scaler.fit_transform(X), columns=X.columns)
    
    # Split the data into train and test sets
    X_train, X_test, y_train, y_test = train_test_split(X_scaled, y, test_size=0.3, random_state=42)
    
    # Train classifier to identify negative returns
    classifier = train_classifier(X_train, y_train)
    
    # Train regressor for magnitude of returns
    regressor = train_regressor(X_train, y_train)
    
    # Run the financial simulation
    capital_invested, final_capital, y_negative, y_pred_negative = run_simulation(X_test, y_test, classifier, regressor)
    
    # Evaluate performance on negative returns
    mae_negative = mean_absolute_error(y_negative, y_pred_negative)
    loguru.logger.info(f"MAE for negative returns: {mae_negative:.4f}")
    
    # Plot actual vs predicted negative returns and financial result
    plot_predictions(y_negative, y_pred_negative, final_capital, capital_invested)

if __name__ == "__main__":
    main()