"""Alpaca brokerage client for account access and order placement (a ``Broker`` adapter).

Pure infrastructure on ``alpaca-py``: credentials are passed in by the caller;
this module holds no configuration and no trading-strategy logic.
"""

import logging

logger = logging.getLogger(__name__)


class AlpacaClient:
    """Client wrapper around the Alpaca trading API.

    Attributes:
        api: The authenticated Alpaca trading client.
    """

    def __init__(self, api_key: str, api_secret: str, base_url: str) -> None:
        """Initialize the Alpaca trading client from explicit credentials.

        Args:
            api_key: Alpaca API key.
            api_secret: Alpaca API secret.
            base_url: Alpaca trading base URL; a paper URL selects the paper account.
        """
        # Lazy import so the SDK is only required when this client is built.
        from alpaca.trading.client import TradingClient

        self.api = TradingClient(api_key, api_secret, paper='paper' in base_url, url_override=base_url)
        logger.info('Initialized Alpaca API')

    def get_account(self) -> object:
        """Retrieve account information from Alpaca.

        Returns:
            The Alpaca account object.
        """
        account: object = self.api.get_account()
        logger.info(f'Account: {account}')
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
        from alpaca.trading.enums import OrderSide, OrderType, TimeInForce
        from alpaca.trading.requests import OrderRequest

        try:
            order: object = self.api.submit_order(
                OrderRequest(
                    symbol=symbol,
                    qty=float(qty),
                    side=OrderSide(side),
                    type=OrderType(order_type),
                    time_in_force=TimeInForce(time_in_force),
                )
            )
        except Exception as e:
            logger.error(f'Failed to place order: {e}')
            return None
        logger.info(f'Order submitted: {order}')
        return order
