"""Unit tests for the pure infrastructure clients.

Each SDK is replaced with a fake injected into ``sys.modules`` and the client module
is then imported afresh, so it binds the fake at module level. The tests assert that
caller-supplied parameters are threaded straight through to the SDK and that the
clients carry no domain logic of their own.
"""

import importlib
import sys
import types
from datetime import date

import pandas as pd
import pytest
from pydantic import SecretStr


def _fresh(monkeypatch, module):
    """Import ``secform4strategy_clients.<module>`` anew, against the SDK fakes installed so far."""
    monkeypatch.delitem(sys.modules, f'secform4strategy_clients.{module}', raising=False)
    return importlib.import_module(f'secform4strategy_clients.{module}')


# --- AlpacaClient ---
def test_alpaca_threads_credentials_and_places_order(monkeypatch):
    client_calls = {}
    orders = []

    class _FakeTradingClient:
        def __init__(self, api_key, secret_key, paper, url_override):
            client_calls.update(api_key=api_key, secret_key=secret_key, paper=paper, url_override=url_override)

        def submit_order(self, order_data):
            orders.append(order_data)
            return 'order'

    def _module(name, **attrs):
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)

    _module('alpaca')
    _module('alpaca.trading')
    _module('alpaca.trading.client', TradingClient=_FakeTradingClient)
    _module('alpaca.trading.enums', OrderSide=str, OrderType=str, TimeInForce=str)
    _module('alpaca.trading.requests', OrderRequest=lambda **fields: fields)

    client = _fresh(monkeypatch, 'broker').AlpacaClient(
        api_key=SecretStr('k'), api_secret=SecretStr('s'), base_url='https://paper-api.alpaca.markets'
    )
    assert client_calls == {
        'api_key': 'k',
        'secret_key': 's',
        'paper': True,
        'url_override': 'https://paper-api.alpaca.markets',
    }

    assert client.place_order('AAPL', 1, 'sell', 'market', 'gtc') == 'order'
    assert orders == [{'symbol': 'AAPL', 'qty': 1.0, 'side': 'sell', 'type': 'market', 'time_in_force': 'gtc'}]


# --- KafkaConsumer ---
class _FakeMessage:
    def __init__(self, payload: bytes, offset: int = 0):
        self._payload = payload
        self._offset = offset

    def value(self):
        return self._payload

    def offset(self):
        return self._offset


class _FakeConsumer:
    def __init__(self, messages):
        self._messages = list(messages)
        self.subscribed = None
        self.committed = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def subscribe(self, topics):
        self.subscribed = topics

    def poll(self, timeout):
        return self._messages.pop(0) if self._messages else None

    def commit(self, offsets):
        self.committed = offsets


class _FakeTopic:
    def __init__(self, name):
        self.name = name


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


class _SerializedTopic(_FakeTopic):
    def serialize(self, key, value):
        # Echo back key/value so the producer test can assert on them.
        return type('_Msg', (), {'key': key, 'value': value})()


class _FakeApplication:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.consumer = None
        self.producer = _FakeProducer()

    def topic(self, name, value_deserializer=None, key_deserializer=None, value_serializer=None, key_serializer=None):
        return _SerializedTopic(name)

    def get_consumer(self, auto_commit_enable):
        return self.consumer

    def get_producer(self):
        return self.producer


def _install_fake_quixstreams(monkeypatch, messages):
    fake = types.ModuleType('quixstreams')
    holder = {}

    def _app_factory(**kwargs):
        app = _FakeApplication(**kwargs)
        app.consumer = _FakeConsumer(messages)
        holder['app'] = app
        return app

    fake.Application = _app_factory
    monkeypatch.setitem(sys.modules, 'quixstreams', fake)
    return holder


def test_kafka_returns_raw_dicts_without_parsing(monkeypatch):
    # A 'date' field stays a raw string — the client must NOT parse it.
    msgs = [
        _FakeMessage(b'{"a": 1, "date": "2026-01-01"}'),
        _FakeMessage(b'{"a": 2, "date": "2026-01-02"}'),
    ]
    holder = _install_fake_quixstreams(monkeypatch, msgs)

    KafkaClient = _fresh(monkeypatch, 'messaging').KafkaClient

    consumer = KafkaClient(broker_address='b:9092', consumer_group='g', auto_offset_reset='earliest')
    assert holder['app'].kwargs['broker_address'] == 'b:9092'
    assert holder['app'].kwargs['consumer_group'] == 'g'

    finished, records = consumer.consume_batch('td', buffer_size=10, timeout=1)
    assert finished is True  # topic exhausted before buffer filled
    assert records == [
        {'a': 1, 'date': '2026-01-01'},
        {'a': 2, 'date': '2026-01-02'},
    ]


def test_kafka_returns_full_buffer_not_finished(monkeypatch):
    msgs = [_FakeMessage(b'{"a": 1}'), _FakeMessage(b'{"a": 2}'), _FakeMessage(b'{"a": 3}')]
    _install_fake_quixstreams(monkeypatch, msgs)

    KafkaClient = _fresh(monkeypatch, 'messaging').KafkaClient

    consumer = KafkaClient(broker_address='b', consumer_group='g', auto_offset_reset='earliest')
    finished, records = consumer.consume_batch('td', buffer_size=2, timeout=1)
    assert finished is False  # buffer filled
    assert records == [{'a': 1}, {'a': 2}]


def test_kafka_producer_serializes_and_flushes(monkeypatch):
    holder = _install_fake_quixstreams(monkeypatch, [])

    KafkaClient = _fresh(monkeypatch, 'messaging').KafkaClient

    client = KafkaClient(broker_address='b:9092')
    client.produce(
        'out',
        [('k1', {'a': 1}), ('k2', {'a': 2})],
    )
    producer = holder['app'].producer
    assert producer.flushed == 1
    assert [(p['key'], p['value']) for p in producer.produced] == [('k1', {'a': 1}), ('k2', {'a': 2})]


def test_kafka_consume_with_offsets_and_commit(monkeypatch):
    msgs = [_FakeMessage(b'{"a": 1}', offset=5), _FakeMessage(b'{"a": 2}', offset=6)]
    holder = _install_fake_quixstreams(monkeypatch, msgs)

    captured = {}

    class _TopicPartition:
        def __init__(self, topic, partition, offset):
            captured.update(topic=topic, partition=partition, offset=offset)

    ck = types.ModuleType('confluent_kafka')
    ck.TopicPartition = _TopicPartition
    monkeypatch.setitem(sys.modules, 'confluent_kafka', ck)

    KafkaClient = _fresh(monkeypatch, 'messaging').KafkaClient

    client = KafkaClient(broker_address='b', consumer_group='g', auto_offset_reset='earliest')
    batch = client.consume_with_offsets('td', buffer_size=10, timeout=1)
    assert batch == [({'a': 1}, 5), ({'a': 2}, 6)]  # raw records paired with offsets

    client.commit_offset(6)  # commits offset + 1
    assert captured == {'topic': 'td', 'partition': 0, 'offset': 7}
    assert holder['app'].consumer.committed is not None


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
            raise ValueError('delisted')
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
        raise ValueError('nope')

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
