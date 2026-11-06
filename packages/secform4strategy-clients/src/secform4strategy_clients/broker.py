"""Alpaca brokerage client for account access and order placement (a ``Broker`` adapter).

Pure infrastructure on ``alpaca-py``: credentials are passed in by the caller;
this module holds no configuration and no trading-strategy logic. Orders carry a client order
id, so Alpaca rejects a repeat and a retried submission never places a second order.
"""

import logging
from http import HTTPStatus
from typing import TYPE_CHECKING

import requests
from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide
from alpaca.trading.enums import OrderType as AlpacaOrderType
from alpaca.trading.enums import TimeInForce as AlpacaTimeInForce
from alpaca.trading.requests import OrderRequest

from secform4strategy_clients.ports import PermanentError, RetryableError
from secform4strategy_clients.retry import with_backoff

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
        self,
        symbol: str,
        qty: float,
        side: Side,
        order_type: OrderType,
        time_in_force: TimeInForce,
        client_order_id: str,
    ) -> object | None:
        """Submit an order to Alpaca, retrying transient failures with backoff.

        Args:
            symbol: Ticker symbol to trade.
            qty: Number of shares to trade.
            side: Order side.
            order_type: Order type.
            time_in_force: Time-in-force policy; ``opg``/``cls`` join the opening or closing auction.
            client_order_id: The order's own id; Alpaca accepts each id once.

        Returns:
            The submitted order, or None if an order with ``client_order_id`` was placed before.
        """
        request = OrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide(side),
            type=AlpacaOrderType(order_type),
            time_in_force=AlpacaTimeInForce(time_in_force),
            client_order_id=client_order_id,
        )
        return with_backoff(lambda: self._submit_once(request))

    def _submit_once(self, request: OrderRequest) -> object | None:
        """Submit an order once and classify a failure.

        Args:
            request: The order, carrying its client order id.

        Returns:
            The submitted order, or None if its client order id was placed before.

        Raises:
            RetryableError: The connection failed, or Alpaca answered 429 or 5xx.
            PermanentError: Alpaca rejected the order, e.g. an unknown or unshortable symbol.
        """
        try:
            order: object = self.api.submit_order(request)
        except requests.RequestException as error:
            raise RetryableError(f'Order {request.client_order_id}: {error!r}') from error
        except APIError as error:
            if error.status_code == HTTPStatus.UNPROCESSABLE_ENTITY and 'client_order_id must be unique' in str(error):
                logger.info(f'Order {request.client_order_id} was placed before')
                return None
            if (
                error.status_code == HTTPStatus.TOO_MANY_REQUESTS
                or error.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR
            ):
                raise RetryableError(f'Order {request.client_order_id}: HTTP {error.status_code} {error}') from error
            raise PermanentError(f'Order {request.client_order_id}: HTTP {error.status_code} {error}') from error
        logger.info(f'Order submitted: {order}')
        return order
