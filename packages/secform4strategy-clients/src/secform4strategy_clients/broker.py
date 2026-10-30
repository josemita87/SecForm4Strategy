"""Alpaca brokerage client for account access and order placement (a ``Broker`` adapter).

Pure infrastructure on ``alpaca-py``: credentials are passed in by the caller;
this module holds no configuration and no trading-strategy logic.
"""

import logging
from typing import TYPE_CHECKING

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide
from alpaca.trading.enums import OrderType as AlpacaOrderType
from alpaca.trading.enums import TimeInForce as AlpacaTimeInForce
from alpaca.trading.requests import OrderRequest

if TYPE_CHECKING:
    from pydantic import SecretStr

    from secform4strategy_clients.ports import OrderType, Side, TimeInForce

logger = logging.getLogger(__name__)


class AlpacaClient:
    """Client wrapper around the Alpaca trading API.

    Attributes:
        api: The authenticated Alpaca trading client.
    """

    def __init__(self, api_key: SecretStr, api_secret: SecretStr, base_url: str) -> None:
        """Initialize the Alpaca trading client; the secrets are unwrapped only here, for the SDK.

        Args:
            api_key: Alpaca API key.
            api_secret: Alpaca API secret.
            base_url: Alpaca trading base URL; a paper URL selects the paper account.
        """
        self.api = TradingClient(
            api_key.get_secret_value(),
            api_secret.get_secret_value(),
            paper='paper' in base_url,
            url_override=base_url,
        )
        logger.info('Initialized Alpaca API')

    def place_order(
        self, symbol: str, qty: float, side: Side, order_type: OrderType, time_in_force: TimeInForce
    ) -> object | None:
        """Submit an order to Alpaca.

        Args:
            symbol: Ticker symbol to trade.
            qty: Number of shares to trade.
            side: Order side.
            order_type: Order type.
            time_in_force: Time-in-force policy; ``opg``/``cls`` join the opening or closing auction.

        Returns:
            The submitted order object, or ``None`` if submission failed.
        """
        try:
            order: object = self.api.submit_order(
                OrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=OrderSide(side),
                    type=AlpacaOrderType(order_type),
                    time_in_force=AlpacaTimeInForce(time_in_force),
                )
            )
        except Exception as e:
            logger.error(f'Failed to place order: {e}')
            return None
        logger.info(f'Order submitted: {order}')
        return order
