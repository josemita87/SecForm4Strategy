"""Unit tests for the pure infrastructure clients.

Each SDK is replaced with a fake injected into ``sys.modules`` so the tests run
without the real (heavy) dependencies. The tests assert that caller-supplied
parameters are threaded straight through to the SDK and that the clients carry
no domain logic of their own.
"""

import sys
import types

import pytest


# --------------------------------------------------------------------------- #
# AlpacaClient
# --------------------------------------------------------------------------- #
def test_alpaca_threads_credentials_and_places_order(monkeypatch):
    rest_calls = {}
    order_calls = {}

    class _FakeREST:
        def __init__(self, key, secret, base_url, api_version):
            rest_calls.update(key=key, secret=secret, base_url=base_url, api_version=api_version)

        def submit_order(self, **kwargs):
            order_calls.update(kwargs)
            return 'order'

    fake = types.ModuleType('alpaca_trade_api')
    fake.REST = _FakeREST
    monkeypatch.setitem(sys.modules, 'alpaca_trade_api', fake)

    from inside_out_clients.broker import AlpacaClient

    client = AlpacaClient(api_key='k', api_secret='s', base_url='http://x')
    assert rest_calls == {
        'key': 'k',
        'secret': 's',
        'base_url': 'http://x',
        'api_version': 'v2',
    }

    result = client.place_order('AAPL', 1, 'sell', 'market', 'gtc')
    assert result == 'order'
    assert order_calls == {
        'symbol': 'AAPL',
        'qty': 1,
        'side': 'sell',
        'type': 'market',
        'time_in_force': 'gtc',
    }


# --------------------------------------------------------------------------- #
# KafkaConsumer
# --------------------------------------------------------------------------- #
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

    def produce(self, topic, value, key, timestamp):
        self.produced.append({'topic': topic, 'value': value, 'key': key, 'timestamp': timestamp})

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

    from inside_out_clients.messaging import KafkaClient

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

    from inside_out_clients.messaging import KafkaClient

    consumer = KafkaClient(broker_address='b', consumer_group='g', auto_offset_reset='earliest')
    finished, records = consumer.consume_batch('td', buffer_size=2, timeout=1)
    assert finished is False  # buffer filled
    assert records == [{'a': 1}, {'a': 2}]


def test_kafka_producer_serializes_and_flushes(monkeypatch):
    holder = _install_fake_quixstreams(monkeypatch, [])

    from inside_out_clients.messaging import KafkaClient

    client = KafkaClient(broker_address='b:9092')
    client.produce(
        'out',
        [('k1', {'a': 1}, 111), ('k2', {'a': 2}, 222)],
    )
    producer = holder['app'].producer
    assert producer.flushed == 1
    assert [(p['key'], p['value'], p['timestamp']) for p in producer.produced] == [
        ('k1', {'a': 1}, 111),
        ('k2', {'a': 2}, 222),
    ]


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

    from inside_out_clients.messaging import KafkaClient

    client = KafkaClient(broker_address='b', consumer_group='g', auto_offset_reset='earliest')
    batch = client.consume_with_offsets('td', buffer_size=10, timeout=1)
    assert batch == [({'a': 1}, 5), ({'a': 2}, 6)]  # raw records paired with offsets

    client.commit_offset(6)  # commits offset + 1
    assert captured == {'topic': 'td', 'partition': 0, 'offset': 7}
    assert holder['app'].consumer.committed is not None


# --------------------------------------------------------------------------- #
# MarketDataClient
# --------------------------------------------------------------------------- #
def test_market_data_reads_info_fields(monkeypatch):
    class _FakeTicker:
        def __init__(self, symbol):
            self.info = (
                {'marketCap': 123, 'exchange': 'NMS'} if symbol == 'AAPL' else {}
            )

    fake = types.ModuleType('yfinance')
    fake.Ticker = _FakeTicker
    monkeypatch.setitem(sys.modules, 'yfinance', fake)

    from inside_out_clients.market_data import MarketDataClient

    client = MarketDataClient()
    assert client.market_cap('AAPL') == 123
    assert client.exchange('AAPL') == 'NMS'
    # Missing keys return None rather than raising.
    assert client.market_cap('ZZZ') is None
    assert client.exchange('ZZZ') is None


def test_market_data_close_history_returns_tidy_frame(monkeypatch):
    import pandas as pd

    captured = {}

    def _download(symbol, start=None):
        captured['symbol'] = symbol
        captured['start'] = start
        idx = pd.to_datetime(['2024-01-01', '2024-01-02'])
        return pd.DataFrame({'Close': [10.0, 11.0]}, index=idx)

    fake = types.ModuleType('yfinance')
    fake.download = _download
    monkeypatch.setitem(sys.modules, 'yfinance', fake)

    from inside_out_clients.market_data import MarketDataClient

    out = MarketDataClient().close_history('AAPL', start='2024-01-01')
    # Caller's symbol and start thread straight through to yfinance.download.
    assert captured == {'symbol': 'AAPL', 'start': '2024-01-01'}
    # The yfinance 'Close' column is reshaped into a tidy (date, close) frame.
    assert list(out.columns) == ['date', 'close']
    assert out['close'].tolist() == [10.0, 11.0]


# --------------------------------------------------------------------------- #
# load_feature_group_catalog
# --------------------------------------------------------------------------- #
def test_load_feature_group_catalog_injects_version_and_defaults_name(tmp_path):
    from inside_out_clients.feature_store import load_feature_group_catalog

    spec = tmp_path / 'feature_groups.yaml'
    spec.write_text(
        'bi4:\n'
        '  primary_key: [key]\n'
        '  event_time: date\n'
        'custom:\n'
        '  name: override_name\n'
        '  primary_key: [id]\n'
        '  event_time: ts\n'
    )

    catalog = load_feature_group_catalog(spec, version=3)
    # name defaults to the reference key; version is injected from the argument.
    assert catalog['bi4'] == {'name': 'bi4', 'version': 3, 'primary_key': ['key'], 'event_time': 'date'}
    # an explicit name in the template is preserved.
    assert catalog['custom']['name'] == 'override_name'
    assert catalog['custom']['version'] == 3


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-q']))
