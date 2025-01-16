from loguru import logger

def plot_predictions(y_test, y_pred_reg, financial_results, capital_invested):
    """Plot actual vs predicted negative returns and financial results."""
    import matplotlib.pyplot as plt
    import seaborn as sns

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
    plt.figtext(0.99, 0.10, f"Capital Invested: ${capital_invested:.2f}", ha='right', fontsize=12, color='green')
    
    # Save plot
    plt.savefig('/app/src/plots/plot_1.png')
    plt.close()
    logger.info("Plot saved as /app/src/plots/plot_1.png")

def run_simulation(real_returns, y_pred_negative, investment=100, threshold=-0.25):
    """Run a simulation based on the model's predictions."""
    
    
    # Simulate the investment
    gains = 0
    capital_invested = investment
    # For each time the model predicted a negative return
    for i, predicted_change in enumerate(y_pred_negative):
        if predicted_change <= threshold:  
            gains += ((-investment) * real_returns.iloc[i])  # Use the actual pct_change (not predicted) to update capital
            capital_invested += investment  # Update capital invested
            logger.debug(gains)
            
    return capital_invested, gains, real_returns, y_pred_negative