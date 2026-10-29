"""Alpaca brokerage client for account access and order placement.

Pure infrastructure: credentials are passed in by the caller; this module holds
no configuration and no trading-strategy logic.
"""

import logging

logger = logging.getLogger(__name__)


class AlpacaClient:
    """Client wrapper around the Alpaca REST trading API.

    Attributes:
        api: The authenticated Alpaca REST client.
    """

    def __init__(self, api_key: str, api_secret: str, base_url: str, api_version: str = 'v2') -> None:
        """Initialize the Alpaca REST client from explicit credentials.

        Args:
            api_key: Alpaca API key.
            api_secret: Alpaca API secret.
            base_url: Alpaca REST base URL.
            api_version: Alpaca API version (defaults to ``"v2"``).
        """
        # Lazy import so the SDK is only required when this client is built.
        import alpaca_trade_api as tradeapi

        self.api = tradeapi.REST(api_key, api_secret, base_url, api_version=api_version)
        logger.info('Initialized Alpaca API')

    def get_account(self):
        """Retrieve account information from Alpaca.

        Returns:
            The Alpaca account object.
        """
        account = self.api.get_account()
        logger.info(f'Account status: {account.status}')
        return account

    def place_order(self, symbol: str, qty: str, side: str, order_type: str, time_in_force: str) -> object | None:
        """Submit an order to Alpaca.

        Args:
            symbol: Ticker symbol to trade.
            qty: Number of shares to trade.
            side: Order side, e.g. ``"buy"`` or ``"sell"``.
            order_type: Order type, e.g. ``"market"``.
            time_in_force: Time-in-force policy, e.g. ``"gtc"``.

        Returns:
            The submitted order object, or ``None`` if submission failed.
        """
        try:
            order = self.api.submit_order(
                symbol=symbol, qty=qty, side=side, type=order_type, time_in_force=time_in_force
            )
            logger.info(f'Order submitted: {order}')
            return order

        except Exception as e:
            logger.error(f'Failed to place order: {e}')
            return None
