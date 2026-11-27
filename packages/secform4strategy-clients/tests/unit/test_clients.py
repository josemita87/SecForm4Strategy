"""Unit tests for the pure infrastructure clients.

Each SDK is replaced with a fake injected into ``sys.modules`` and the client module
is then imported afresh, so it binds the fake at module level. The tests assert that
caller-supplied parameters are threaded straight through to the SDK and that the
clients carry no domain logic of their own.
"""

import importlib
import sys
import types
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from pydantic import SecretStr
from secform4strategy_clients.ports import PermanentError, RetryableError
from secform4strategy_clients.records import BrokerAccount, BrokerOrder, BrokerPosition, EquityPoint, MarketSession


def _fresh(monkeypatch, module):
    """Import ``secform4strategy_clients.<module>`` anew, against the SDK fakes installed so far."""
    monkeypatch.delitem(sys.modules, f'secform4strategy_clients.{module}', raising=False)
    return importlib.import_module(f'secform4strategy_clients.{module}')


# --- AlpacaClient ---
class _FakeAPIError(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


class _FakeModel(types.SimpleNamespace):
    """An alpaca-py response model: its fields as attributes."""


class _FakeTradeAccount(_FakeModel): ...


class _FakePosition(_FakeModel): ...


class _FakeOrder(_FakeModel): ...


class _FakePortfolioHistory(_FakeModel): ...


class _FakeCalendar(_FakeModel): ...


#: What the fake account reads return: one account, position, order and equity history.
_READS = {
    'account': _FakeTradeAccount(
        equity='1000.5', cash=None, buying_power='2000', status=types.SimpleNamespace(value='ACTIVE')
    ),
    'positions': [
        _FakePosition(
            symbol='AAPL',
            qty='-3',
            avg_entry_price='200',
            current_price='190',
            market_value=None,
            unrealized_pl='30',
            unrealized_plpc='0.05',
        ),
        {'raw': 'skipped'},
    ],
    'orders': [
        _FakeOrder(
            id='o1',
            client_order_id='AAPL@k',
            symbol='AAPL',
            side=types.SimpleNamespace(value='sell'),
            qty='3',
            status=types.SimpleNamespace(value='filled'),
            submitted_at=None,
            filled_at=None,
            filled_avg_price='200',
        )
    ],
    'history': _FakePortfolioHistory(timestamp=[1763650800, 1763737200], equity=[1000.0, None]),
    # alpaca-py parses the calendar's times as naive New York wall-clock times
    'calendar': [
        _FakeCalendar(
            date=date(2026, 11, 27),
            open=datetime.fromisoformat('2026-11-27T09:30'),
            close=datetime.fromisoformat('2026-11-27T13:00'),
        ),
        _FakeCalendar(
            date=date(2026, 11, 30),
            open=datetime.fromisoformat('2026-11-30T09:30'),
            close=datetime.fromisoformat('2026-11-30T16:00'),
        ),
        {'raw': 'skipped'},
    ],
}


def _install_fake_alpaca(monkeypatch, outcomes):
    """Install a fake alpaca-py: ``submit_order`` returns or raises each outcome in turn, ``get_calendar`` raises it."""
    calls = {'client': {}, 'orders': [], 'reads': []}

    class _FakeTradingClient:
        def __init__(self, api_key, secret_key, paper, url_override):
            calls['client'].update(api_key=api_key, secret_key=secret_key, paper=paper, url_override=url_override)

        def submit_order(self, order_data):
            calls['orders'].append(order_data)
            if isinstance(outcome := outcomes.pop(0), Exception):
                raise outcome
            return outcome

        def get_account(self):
            return _READS['account']

        def get_all_positions(self):
            return _READS['positions']

        def get_orders(self, request):
            calls['reads'].append(request)
            return _READS['orders']

        def get_portfolio_history(self, request):
            calls['reads'].append(request)
            return _READS['history']

        def get_calendar(self, filters):
            calls['reads'].append(filters)
            if outcomes:
                raise outcomes.pop(0)
            return _READS['calendar']

    def _module(name, **attrs):
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)

    _module('alpaca')
    _module('alpaca.common')
    _module('alpaca.common.exceptions', APIError=_FakeAPIError)
    _module('alpaca.trading')
    _module('alpaca.trading.client', TradingClient=_FakeTradingClient)
    _module(
        'alpaca.trading.enums',
        OrderSide=str,
        OrderType=str,
        TimeInForce=str,
        QueryOrderStatus=types.SimpleNamespace(ALL='all'),
    )
    _module(
        'alpaca.trading.models',
        Calendar=_FakeCalendar,
        Order=_FakeOrder,
        PortfolioHistory=_FakePortfolioHistory,
        Position=_FakePosition,
        TradeAccount=_FakeTradeAccount,
    )
    _module(
        'alpaca.trading.requests',
        OrderRequest=types.SimpleNamespace,
        GetCalendarRequest=types.SimpleNamespace,
        GetOrdersRequest=types.SimpleNamespace,
        GetPortfolioHistoryRequest=types.SimpleNamespace,
    )
    return calls


def _alpaca(monkeypatch, outcomes):
    calls = _install_fake_alpaca(monkeypatch, outcomes)
    client = _fresh(monkeypatch, 'broker').AlpacaClient(
        api_key=SecretStr('k'), api_secret=SecretStr('s'), base_url='https://paper-api.alpaca.markets'
    )
    return client, calls


def test_alpaca_threads_credentials_and_places_order_with_its_client_id(monkeypatch):
    client, calls = _alpaca(monkeypatch, ['order'])
    assert calls['client'] == {
        'api_key': 'k',
        'secret_key': 's',
        'paper': True,
        'url_override': 'https://paper-api.alpaca.markets',
    }
    assert client.place_order('AAPL', 1, 'sell', 'market', 'gtc', 'AAPL@2026-11-02T14:30Z') == 'order'
    assert vars(calls['orders'][0]) == {
        'symbol': 'AAPL',
        'qty': 1,
        'side': 'sell',
        'type': 'market',
        'time_in_force': 'gtc',
        'client_order_id': 'AAPL@2026-11-02T14:30Z',
    }


@pytest.mark.parametrize(
    'base_url', ['https://api.alpaca.markets', 'https://paper-api.alpaca.markets.example.com', 'https://evil/paper']
)
def test_alpaca_refuses_a_non_paper_url(monkeypatch, base_url):
    calls = _install_fake_alpaca(monkeypatch, [])
    with pytest.raises(ValueError, match='only paper trading'):
        _fresh(monkeypatch, 'broker').AlpacaClient(api_key=SecretStr('k'), api_secret=SecretStr('s'), base_url=base_url)
    assert calls['client'] == {}


def test_alpaca_repeated_client_id_means_already_placed(monkeypatch):
    client, _ = _alpaca(monkeypatch, [_FakeAPIError(422, '{"message": "client_order_id must be unique"}')])
    assert client.place_order('AAPL', 1, 'sell', 'market', 'gtc', 'k') is None


def test_alpaca_server_error_is_retryable_and_not_resubmitted(monkeypatch):
    client, calls = _alpaca(monkeypatch, [_FakeAPIError(503, 'unavailable')])
    with pytest.raises(RetryableError, match='503'):
        client.place_order('AAPL', 1, 'sell', 'market', 'gtc', 'k')
    assert len(calls['orders']) == 1


def test_alpaca_error_without_status_is_retryable(monkeypatch):
    client, _ = _alpaca(monkeypatch, [_FakeAPIError(None, 'no response')])
    with pytest.raises(RetryableError):
        client.place_order('AAPL', 1, 'sell', 'market', 'gtc', 'k')


def test_alpaca_other_client_id_rejection_is_permanent(monkeypatch):
    client, _ = _alpaca(monkeypatch, [_FakeAPIError(422, '{"message": "client_order_id is too long"}')])
    with pytest.raises(PermanentError, match='422'):
        client.place_order('AAPL', 1, 'sell', 'market', 'gtc', 'k')


def test_alpaca_rejection_is_permanent(monkeypatch):
    client, calls = _alpaca(monkeypatch, [_FakeAPIError(403, 'not shortable')])
    with pytest.raises(PermanentError, match='403'):
        client.place_order('AAPL', 1, 'sell', 'market', 'gtc', 'k')
    assert len(calls['orders']) == 1


def test_alpaca_reads_parse_the_account(monkeypatch):
    client, calls = _alpaca(monkeypatch, [])
    assert client.paper is True
    assert client.account() == BrokerAccount(1000.5, 0.0, 2000.0, 'ACTIVE')
    assert client.positions() == (BrokerPosition('AAPL', -3.0, 200.0, 190.0, None, 30.0, 0.05),)
    assert client.orders(50) == (BrokerOrder('o1', 'AAPL@k', 'AAPL', 'sell', 3.0, 'filled', None, None, 200.0),)
    assert client.equity_history('1M', '1D') == (EquityPoint(datetime.fromtimestamp(1763650800, UTC), 1000.0),)
    assert [vars(request) for request in calls['reads']] == [
        {'status': 'all', 'limit': 50},
        {'period': '1M', 'timeframe': '1D'},
    ]


def test_alpaca_sessions_are_new_york_times(monkeypatch):
    client, calls = _alpaca(monkeypatch, [])
    new_york = ZoneInfo('America/New_York')
    assert client.sessions(date(2026, 11, 27), date(2026, 11, 30)) == (
        MarketSession(datetime(2026, 11, 27, 9, 30, tzinfo=new_york), datetime(2026, 11, 27, 13, 0, tzinfo=new_york)),
        MarketSession(datetime(2026, 11, 30, 9, 30, tzinfo=new_york), datetime(2026, 11, 30, 16, 0, tzinfo=new_york)),
    )
    assert client.sessions(date(2026, 11, 27), date(2026, 11, 30))[0].close == datetime(2026, 11, 27, 18, 0, tzinfo=UTC)
    assert vars(calls['reads'][0]) == {'start': date(2026, 11, 27), 'end': date(2026, 11, 30)}


@pytest.mark.parametrize(('status', 'error'), [(503, RetryableError), (None, RetryableError), (403, PermanentError)])
def test_alpaca_calendar_errors_are_classified(monkeypatch, status, error):
    client, _ = _alpaca(monkeypatch, [_FakeAPIError(status, 'calendar')])
    with pytest.raises(error, match='Calendar'):
        client.sessions(date(2026, 11, 27), date(2026, 11, 30))


# --- KafkaClient ---
class _FakeMessage:
    def __init__(self, payload: bytes, offset: int = 0, partition: int = 0):
        self._payload = payload
        self._offset = offset
        self._partition = partition

    def value(self):
        return self._payload

    def offset(self):
        return self._offset

    def partition(self):
        return self._partition

    def error(self):
        return None


class _FakeConsumer:
    def __init__(self, messages):
        self._messages = list(messages)
        self.subscribed = None
        self.committed = None
        self.asynchronous = None
        self.sought = []
        self.closed = False
        self.timeouts = []

    def subscribe(self, topics):
        self.subscribed = topics

    def poll(self, timeout):
        self.timeouts.append(timeout)
        return self._messages.pop(0) if self._messages else None

    def commit(self, offsets, asynchronous):
        self.committed, self.asynchronous = offsets, asynchronous

    def seek(self, partition):
        self.sought.append(partition)

    def close(self):
        self.closed = True


class _FakeProducer:
    def __init__(self):
        self.produced = []
        self.flushed = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def produce(self, topic, value, key):
        self.produced.append({'topic': topic, 'value': value, 'key': key})

    def flush(self):
        self.flushed += 1


class _FakeTopic:
    def __init__(self, name):
        self.name = name

    def serialize(self, key, value):
        # Echo back key/value so the producer test can assert on them.
        return type('_Msg', (), {'key': key, 'value': value})()


class _FakeApplication:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.consumer = None
        self.producer = _FakeProducer()

    def topic(self, name, value_serializer=None, key_serializer=None):
        return _FakeTopic(name)

    def get_consumer(self, auto_commit_enable):
        assert auto_commit_enable is False
        return self.consumer

    def get_producer(self):
        return self.producer


def _install_fake_kafka(monkeypatch, messages):
    fake = types.ModuleType('quixstreams')
    holder = {}

    def _app_factory(**kwargs):
        app = _FakeApplication(**kwargs)
        app.consumer = _FakeConsumer(messages)
        holder['app'] = app
        return app

    fake.Application = _app_factory
    monkeypatch.setitem(sys.modules, 'quixstreams', fake)
    ck = types.ModuleType('confluent_kafka')
    ck.TopicPartition = lambda topic, partition, offset: (topic, partition, offset)
    ck.KafkaException = RuntimeError
    monkeypatch.setitem(sys.modules, 'confluent_kafka', ck)
    return holder


def test_kafka_receive_returns_raw_bodies_until_a_poll_is_empty(monkeypatch):
    # A 'date' field stays a raw string: the client must not parse it.
    msgs = [_FakeMessage(b'{"a": 1, "date": "2026-01-01"}', offset=5), _FakeMessage(b'{"a": 2}', offset=6)]
    holder = _install_fake_kafka(monkeypatch, msgs)
    client = _fresh(monkeypatch, 'messaging').KafkaClient(
        broker_address='b:9092', consumer_group='g', auto_offset_reset='earliest'
    )
    assert holder['app'].kwargs['broker_address'] == 'b:9092'
    assert holder['app'].kwargs['consumer_group'] == 'g'

    messages = client.receive('td', max_messages=10, wait_seconds=1)
    assert [m.body for m in messages] == [{'a': 1, 'date': '2026-01-01'}, {'a': 2}]
    assert [m.receipt for m in messages] == [(0, 5), (0, 6)]
    assert holder['app'].consumer.subscribed == ['td']
    # Only the first poll waits; once messages are in hand, polls do not
    assert holder['app'].consumer.timeouts == [1, 0, 0]
    assert client.receive('td', max_messages=10, wait_seconds=1) == ()


def test_kafka_receive_stops_at_max_messages(monkeypatch):
    _install_fake_kafka(monkeypatch, [_FakeMessage(b'{"a": 1}'), _FakeMessage(b'{"a": 2}'), _FakeMessage(b'{"a": 3}')])
    client = _fresh(monkeypatch, 'messaging').KafkaClient(broker_address='b', consumer_group='g')
    assert [m.body for m in client.receive('td', max_messages=2, wait_seconds=1)] == [{'a': 1}, {'a': 2}]
    assert [m.body for m in client.receive('td', max_messages=2, wait_seconds=1)] == [{'a': 3}]


def test_kafka_receive_raises_a_broker_error(monkeypatch):
    class _ErrorEvent(_FakeMessage):
        def error(self):
            return 'broker transport failure'

    _install_fake_kafka(monkeypatch, [_ErrorEvent(b'')])
    client = _fresh(monkeypatch, 'messaging').KafkaClient(broker_address='b', consumer_group='g')
    with pytest.raises(RuntimeError, match='broker transport failure'):
        client.receive('td', max_messages=10, wait_seconds=1)


def test_kafka_ack_commits_past_the_highest_offset_per_partition(monkeypatch):
    msgs = [
        _FakeMessage(b'{}', offset=7, partition=0),
        _FakeMessage(b'{}', offset=5, partition=0),
        _FakeMessage(b'{}', offset=2, partition=1),
    ]
    holder = _install_fake_kafka(monkeypatch, msgs)
    client = _fresh(monkeypatch, 'messaging').KafkaClient(broker_address='b', consumer_group='g')

    client.ack('td', client.receive('td', max_messages=10, wait_seconds=1))
    assert holder['app'].consumer.committed == [('td', 0, 8), ('td', 1, 3)]
    assert holder['app'].consumer.asynchronous is False
    assert holder['app'].consumer.sought == []


def test_kafka_ack_stops_at_the_first_unacked_message_and_seeks_back_to_it(monkeypatch):
    msgs = [_FakeMessage(b'{}', offset=offset) for offset in (4, 5, 6)]
    holder = _install_fake_kafka(monkeypatch, msgs)
    client = _fresh(monkeypatch, 'messaging').KafkaClient(broker_address='b', consumer_group='g')

    first, _, last = client.receive('td', max_messages=10, wait_seconds=1)
    client.ack('td', [first, last])
    assert holder['app'].consumer.committed == [('td', 0, 5)]
    assert holder['app'].consumer.sought == [('td', 0, 5)]


def test_kafka_ack_rejects_a_message_not_received_since_the_last_ack(monkeypatch):
    _install_fake_kafka(monkeypatch, [_FakeMessage(b'{}', offset=1)])
    client = _fresh(monkeypatch, 'messaging').KafkaClient(broker_address='b', consumer_group='g')
    received = client.receive('td', max_messages=10, wait_seconds=1)
    client.ack('td', received)
    with pytest.raises(ValueError, match='since the last ack'):
        client.ack('td', received)


def test_kafka_drops_a_message_that_is_not_json_and_counts_it_as_acked(monkeypatch):
    msgs = [_FakeMessage(b'not json', offset=3), _FakeMessage(b'{"a": 1}', offset=4)]
    holder = _install_fake_kafka(monkeypatch, msgs)
    client = _fresh(monkeypatch, 'messaging').KafkaClient(broker_address='b', consumer_group='g')

    received = client.receive('td', max_messages=10, wait_seconds=1)
    assert [m.body for m in received] == [{'a': 1}]
    client.ack('td', received)
    assert holder['app'].consumer.committed == [('td', 0, 5)]


def test_kafka_ack_raises_when_a_partition_fails_to_commit(monkeypatch):
    holder = _install_fake_kafka(monkeypatch, [_FakeMessage(b'{}', offset=1)])
    client = _fresh(monkeypatch, 'messaging').KafkaClient(broker_address='b', consumer_group='g')
    received = client.receive('td', max_messages=10, wait_seconds=1)
    holder['app'].consumer.commit = lambda **_: [types.SimpleNamespace(error='rebalanced')]
    with pytest.raises(RuntimeError, match='Commit failed'):
        client.ack('td', received)


def test_kafka_close_closes_the_consumers(monkeypatch):
    holder = _install_fake_kafka(monkeypatch, [])
    client = _fresh(monkeypatch, 'messaging').KafkaClient(broker_address='b', consumer_group='g')
    client.receive('td', max_messages=1, wait_seconds=1)
    client.close()
    assert holder['app'].consumer.closed is True


def test_kafka_send_serializes_and_flushes(monkeypatch):
    holder = _install_fake_kafka(monkeypatch, [])
    client = _fresh(monkeypatch, 'messaging').KafkaClient(broker_address='b:9092')
    client.send('out', [('k1', {'a': 1}), ('k2', {'a': 2})])
    producer = holder['app'].producer
    assert producer.flushed == 1
    assert [(p['key'], p['value']) for p in producer.produced] == [('k1', {'a': 1}), ('k2', {'a': 2})]


# --- MarketDataClient ---
def test_market_data_daily_bars_returns_tidy_frame(monkeypatch):
    captured = {}

    def _download(symbol, **kwargs):
        captured.update(symbol=symbol, **kwargs)
        # A 2-for-1 split takes effect on day 3; Close is split-adjusted, Adj Close also dividend-adjusted
        idx = pd.to_datetime(['2024-01-01', '2024-01-02', '2024-01-03'])
        return pd.DataFrame(
            {
                'Open': [9.5, 10.5, 10.0],
                'Close': [10.0, 11.0, 12.0],
                'Adj Close': [9.0, 9.9, 12.0],
                'Stock Splits': [0.0, 0.0, 2.0],
                'Dividends': [0.0, 0.25, 0.0],
            },
            index=idx,
        )

    fake = types.ModuleType('yfinance')
    fake.download = _download
    monkeypatch.setitem(sys.modules, 'yfinance', fake)

    MarketDataClient = _fresh(monkeypatch, 'market_data').MarketDataClient

    out = MarketDataClient().daily_bars('AAPL', start=date(2024, 1, 1))
    # Caller's symbol and start thread straight through to yfinance.download, with flat columns.
    assert captured == {
        'symbol': 'AAPL',
        'start': date(2024, 1, 1),
        'auto_adjust': False,
        'actions': True,
        'multi_level_index': False,
    }
    # Without a start, the full history is requested rather than yfinance's one-month default.
    captured.clear()
    MarketDataClient().daily_bars('AAPL')
    assert captured['period'] == 'max' and 'start' not in captured
    # Adjusted open/close (as auto_adjust computes them), and the close as traded before the split.
    assert list(out.columns) == ['date', 'open', 'close', 'close_as_traded', 'split', 'dividend']
    assert out['open'].round(6).tolist() == [8.55, 9.45, 10.0]
    assert out['close'].tolist() == [9.0, 9.9, 12.0]
    assert out['close_as_traded'].tolist() == [20.0, 22.0, 12.0]
    # The day's corporate actions pass through
    assert out['split'].tolist() == [0.0, 0.0, 2.0]
    assert out['dividend'].tolist() == [0.0, 0.25, 0.0]


def test_market_data_price_histories_tags_skips_and_downcasts(monkeypatch):
    starts_seen = {}

    def _download(symbol, start=None, **kwargs):
        starts_seen[symbol] = start
        if symbol == 'BAD':
            return pd.DataFrame()
        idx = pd.to_datetime(['2024-01-02', '2024-01-01'])  # deliberately unsorted
        return pd.DataFrame(
            {
                'Open': [10.5, 9.5],
                'Close': [11.0, 10.0],
                'Adj Close': [11.0, 10.0],
                'Stock Splits': [0.0, 0.0],
                'Dividends': [0.0, 0.0],
            },
            index=idx,
        )

    fake = types.ModuleType('yfinance')
    fake.download = _download
    monkeypatch.setitem(sys.modules, 'yfinance', fake)

    MarketDataClient = _fresh(monkeypatch, 'market_data').MarketDataClient

    out = MarketDataClient().price_histories(['AAPL', 'BAD', 'MSFT'], starts={'AAPL': date(2024, 1, 1)})

    # Per-symbol starts thread through; symbols absent from the mapping fetch full history.
    assert starts_seen == {'AAPL': date(2024, 1, 1), 'BAD': None, 'MSFT': None}
    # The failed symbol is skipped, not fatal; survivors are tagged by ticker.
    assert set(out['ticker']) == {'AAPL', 'MSFT'}
    # Storage-friendly dtypes and (ticker, date) ordering.
    assert out['open'].dtype == 'float32'
    assert out['close'].dtype == 'float32'
    assert out['close_as_traded'].dtype == 'float32'
    assert isinstance(out['ticker'].dtype, pd.CategoricalDtype)
    aapl = out[out['ticker'] == 'AAPL']
    assert aapl['date'].is_monotonic_increasing


def test_market_data_price_histories_empty_when_all_fail(monkeypatch):
    def _download(symbol, **kwargs):
        return pd.DataFrame()

    fake = types.ModuleType('yfinance')
    fake.download = _download
    monkeypatch.setitem(sys.modules, 'yfinance', fake)

    MarketDataClient = _fresh(monkeypatch, 'market_data').MarketDataClient

    out = MarketDataClient().price_histories(['BAD'])
    assert list(out.columns) == ['date', 'open', 'close', 'close_as_traded', 'split', 'dividend', 'ticker']
    assert out.empty


# --- load_feature_group_catalog ---
def test_load_feature_group_catalog_injects_version_and_defaults_name(tmp_path):
    from secform4strategy_clients.feature_groups import FeatureGroupSpec, load_feature_group_catalog

    spec = tmp_path / 'spec.yaml'
    spec.write_text(
        'feature_groups:\n'
        '  bi4:\n'
        '    primary_key: [key]\n'
        '    event_time: date\n'
        '  custom:\n'
        '    name: override_name\n'
        '    primary_key: [id]\n'
        '    event_time: ts\n'
        'other_section: 1\n'
    )

    catalog = load_feature_group_catalog(spec, version=3)
    # name defaults to the reference key; version is injected from the argument.
    assert catalog['bi4'] == FeatureGroupSpec(name='bi4', version=3, primary_key=('key',), event_time='date')
    # an explicit name in the template is preserved.
    assert catalog['custom'].name == 'override_name'
    assert catalog['custom'].version == 3


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-q']))
