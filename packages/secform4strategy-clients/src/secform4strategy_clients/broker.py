"""Alpaca brokerage client for orders, account reads and the market calendar.

It is a ``Broker``, ``TradingAccount`` and ``MarketCalendar`` adapter.

Pure infrastructure on ``alpaca-py``: credentials are passed in by the caller;
this module holds no configuration and no trading-strategy logic. Orders carry a client order
id, so Alpaca rejects a repeat and a rerun never places a second order. alpaca-py retries 429 and
504 itself, so the client only classifies what is left. alpaca-py returns numbers as strings, so the
reads parse them into records.
"""

import logging
from datetime import UTC, date, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests
from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus
from alpaca.trading.enums import OrderType as AlpacaOrderType
from alpaca.trading.enums import TimeInForce as AlpacaTimeInForce
from alpaca.trading.models import Calendar, Order, PortfolioHistory, Position, TradeAccount
from alpaca.trading.requests import GetCalendarRequest, GetOrdersRequest, GetPortfolioHistoryRequest, OrderRequest

from secform4strategy_clients.ports import PermanentError, RetryableError, UntradableError
from secform4strategy_clients.records import BrokerAccount, BrokerOrder, BrokerPosition, EquityPoint, MarketSession

if TYPE_CHECKING:
    from pydantic import SecretStr

    from secform4strategy_clients.ports import OrderType, Side, TimeInForce

logger = logging.getLogger(__name__)

#: Alpaca's calendar gives session times as naive New York wall-clock times
EXCHANGE_TZ = ZoneInfo('America/New_York')

#: The only trading host the client accepts, so no configuration can reach a live account
PAPER_HOST = 'paper-api.alpaca.markets'


def _number(value: str | float | None) -> float | None:
    """Return Alpaca's numeric string as a float, or None when absent."""
    return None if value is None else float(value)


def _transient(error: APIError) -> bool:
    """Return whether an Alpaca API error is worth repeating: no status, 429 or 5xx."""
    return (
        (status := error.status_code) is None
        or status == HTTPStatus.TOO_MANY_REQUESTS
        or status >= HTTPStatus.INTERNAL_SERVER_ERROR
    )


class AlpacaClient:
    """Client wrapper around the Alpaca trading API.

    Attributes:
        api: The authenticated Alpaca trading client.
        paper: Whether the account is a paper-trading account; always True.
    """

    def __init__(self, api_key: SecretStr, api_secret: SecretStr, base_url: str) -> None:
        """Initialize the Alpaca trading client; the secrets are unwrapped only here, for the SDK.

        Args:
            api_key: Alpaca API key.
            api_secret: Alpaca API secret.
            base_url: Alpaca trading base URL; its host must be the paper host.

        Raises:
            ValueError: If ``base_url`` is not on the paper host.
        """
        if urlsplit(base_url).hostname != PAPER_HOST:
            raise ValueError(f'Alpaca base URL {base_url} is not on {PAPER_HOST}; only paper trading is allowed')
        self.paper = True
        self.api = TradingClient(
            api_key.get_secret_value(),
            api_secret.get_secret_value(),
            paper=True,
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
        """Submit an order to Alpaca once, classifying a failure.

        Args:
            symbol: Ticker symbol to trade.
            qty: Number of shares to trade.
            side: Order side.
            order_type: Order type.
            time_in_force: Time-in-force policy; ``opg``/``cls`` join the opening or closing auction.
            client_order_id: The order's own id; Alpaca accepts each id once.

        Returns:
            The submitted order, or None if an order with ``client_order_id`` was placed before.

        Raises:
            RetryableError: The connection failed, Alpaca gave no status, or it answered 429 or 5xx after its retries.
            UntradableError: Alpaca does not trade or short the symbol.
            PermanentError: Alpaca rejected the order for another reason, e.g. an unknown symbol.
        """
        try:
            order: object = self.api.submit_order(
                OrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=OrderSide(side),
                    type=AlpacaOrderType(order_type),
                    time_in_force=AlpacaTimeInForce(time_in_force),
                    client_order_id=client_order_id,
                )
            )
        except requests.RequestException as error:
            raise RetryableError(f'Order {client_order_id}: {error!r}') from error
        except APIError as error:
            if error.status_code == HTTPStatus.UNPROCESSABLE_ENTITY and 'client_order_id must be unique' in str(error):
                logger.info(f'Order {client_order_id} was placed before')
                return None
            if _transient(error):
                raise RetryableError(f'Order {client_order_id}: HTTP {error.status_code} {error}') from error
            if error.status_code == HTTPStatus.UNPROCESSABLE_ENTITY and any(
                reason in str(error) for reason in ('not tradable', 'not shortable', 'cannot be sold short')
            ):
                raise UntradableError(f'Order {client_order_id}: HTTP {error.status_code} {error}') from error
            raise PermanentError(f'Order {client_order_id}: HTTP {error.status_code} {error}') from error
        logger.info(f'Order submitted: {order}')
        return order

    def account(self) -> BrokerAccount:
        """Return the balances and status.

        Returns:
            The account.

        Raises:
            TypeError: If alpaca-py returned raw data instead of its model.
        """
        if not isinstance(account := self.api.get_account(), TradeAccount):
            raise TypeError(f'Expected a TradeAccount, got {type(account).__name__}')
        return BrokerAccount(
            float(account.equity or 0),
            float(account.cash or 0),
            float(account.buying_power or 0),
            str(account.status.value),
        )

    def positions(self) -> tuple[BrokerPosition, ...]:
        """Return the open positions.

        Returns:
            The positions; a short has a negative quantity.
        """
        return tuple(
            BrokerPosition(
                position.symbol,
                float(position.qty),
                float(position.avg_entry_price),
                _number(position.current_price),
                _number(position.market_value),
                _number(position.unrealized_pl),
                _number(position.unrealized_plpc),
            )
            for position in self.api.get_all_positions()
            if isinstance(position, Position)
        )

    def orders(self, limit: int) -> tuple[BrokerOrder, ...]:
        """Return up to ``limit`` orders of any status, newest first.

        Args:
            limit: At most this many orders, up to 500.

        Returns:
            The orders.
        """
        return tuple(
            BrokerOrder(
                str(order.id),
                order.client_order_id,
                order.symbol or '',
                order.side.value if order.side else '',
                _number(order.qty),
                str(order.status.value),
                order.submitted_at,
                order.filled_at,
                _number(order.filled_avg_price),
            )
            for order in self.api.get_orders(GetOrdersRequest(status=QueryOrderStatus.ALL, limit=limit))
            if isinstance(order, Order)
        )

    def equity_history(self, period: str, timeframe: str) -> tuple[EquityPoint, ...]:
        """Return the equity over ``period`` at ``timeframe`` resolution, oldest first.

        Args:
            period: Alpaca's period, such as ``1M``.
            timeframe: Alpaca's resolution, such as ``1D``.

        Returns:
            One point per window; windows with no equity reported are left out.

        Raises:
            TypeError: If alpaca-py returned raw data instead of its model.
        """
        history = self.api.get_portfolio_history(GetPortfolioHistoryRequest(period=period, timeframe=timeframe))
        if not isinstance(history, PortfolioHistory):
            raise TypeError(f'Expected a PortfolioHistory, got {type(history).__name__}')
        return tuple(
            EquityPoint(datetime.fromtimestamp(stamp, UTC), float(equity))
            for stamp, equity in zip(history.timestamp, history.equity, strict=True)
            if equity is not None
        )

    def sessions(self, start: date, end: date) -> tuple[MarketSession, ...]:
        """Return the regular sessions from ``start`` to ``end``, both included, oldest first.

        Args:
            start: The first day.
            end: The last day.

        Returns:
            One session per trading day, its open and close in New York time.

        Raises:
            RetryableError: The connection failed, Alpaca gave no status, or it answered 429 or 5xx after its retries.
            PermanentError: Alpaca answered any other error.
        """
        try:
            days = self.api.get_calendar(GetCalendarRequest(start=start, end=end))
        except requests.RequestException as error:
            raise RetryableError(f'Calendar {start} to {end}: {error!r}') from error
        except APIError as error:
            if _transient(error):
                raise RetryableError(f'Calendar {start} to {end}: HTTP {error.status_code} {error}') from error
            raise PermanentError(f'Calendar {start} to {end}: HTTP {error.status_code} {error}') from error
        return tuple(
            MarketSession(day.open.replace(tzinfo=EXCHANGE_TZ), day.close.replace(tzinfo=EXCHANGE_TZ))
            for day in days
            if isinstance(day, Calendar)
        )
