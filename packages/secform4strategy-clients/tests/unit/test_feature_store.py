"""Rows come from an Iceberg table, filtered by key or by event time and narrowed to some columns.

A push that loses a commit race to another writer upserts again on the new snapshot: both writes are kept, and a key
both wrote is kept once.

Under test:
    packages/secform4strategy-clients/src/secform4strategy_clients/feature_store.py
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
import pytest
from pyiceberg.catalog.sql import SqlCatalog
from secform4strategy_clients.feature_groups import FeatureGroupSpec
from secform4strategy_clients.feature_store import IcebergFeatureStore

if TYPE_CHECKING:
    from pyiceberg.table import Table

SINCE = datetime(2026, 11, 20, tzinfo=UTC)
SPEC = FeatureGroupSpec(name='sales', version=1, primary_key=('key',), event_time='accepted_at')


class RacingCatalog(SqlCatalog):
    """A SQLite catalog where a rival writer commits right after the next table load, once."""

    rival: Callable[[], object] | None = None

    def load_table(self, identifier: str | tuple[str, ...]) -> Table:
        """Load the table, then let the rival commit before the caller can."""
        table = super().load_table(identifier)
        if (rival := self.rival) is not None:
            self.rival = None
            rival()
        return table


@pytest.fixture
def store(tmp_path: Path) -> IcebergFeatureStore:
    """A feature store on a SQLite catalog in a temporary directory, with one feature group."""
    catalog = RacingCatalog('test', uri=f'sqlite:///{tmp_path}/catalog.db', warehouse=tmp_path.as_uri())
    return IcebergFeatureStore(catalog, 'features', {'sales': SPEC})


def sales(*keys: str) -> pd.DataFrame:
    """One row per key, accepted on 2026-11-21."""
    return pd.DataFrame({'key': list(keys), 'value': 1.0, 'accepted_at': pd.Timestamp('2026-11-21', tz=UTC)})


class TestReadSince:
    """Only rows at or after the start, with only the columns asked for."""

    def test_rows_from_the_start(self, store: IcebergFeatureStore) -> None:
        store.push(
            'sales',
            pd.DataFrame(
                {
                    'key': ['old', 'new'],
                    'value': [1.0, 2.0],
                    'accepted_at': pd.to_datetime(['2026-11-19', '2026-11-21'], utc=True),
                }
            ),
        )
        assert store.read_since('sales', SINCE, ('key',)).to_dict('list') == {'key': ['new']}

    def test_a_missing_table_has_no_rows(self, store: IcebergFeatureStore) -> None:
        rows = store.read_since('sales', SINCE, ('key', 'value'))
        assert (rows.empty, list(rows.columns)) == (True, ['key', 'value'])


class TestRead:
    """A filtered read returns every column, or only the columns asked for."""

    ROWS = pd.DataFrame(
        {
            'key': ['a', 'b'],
            'value': [1.0, 2.0],
            'accepted_at': pd.to_datetime(['2026-11-19', '2026-11-21'], utc=True),
        }
    )

    def test_every_column_by_default(self, store: IcebergFeatureStore) -> None:
        store.push('sales', self.ROWS)
        assert sorted(store.read('sales', where={'key': 'b'}).columns) == ['accepted_at', 'key', 'value']

    def test_only_the_columns_asked_for(self, store: IcebergFeatureStore) -> None:
        store.push('sales', self.ROWS)
        assert store.read('sales', where={'key': ['a', 'b']}, columns=('key',)).sort_values('key').to_dict('list') == {
            'key': ['a', 'b']
        }


class TestPushConflict:
    """A push that loses the commit race upserts again on the new snapshot; no write is lost or doubled."""

    def test_both_writers_rows_are_kept(self, store: IcebergFeatureStore, caplog: pytest.LogCaptureFixture) -> None:
        store.push('sales', sales('seed'))
        catalog = store.catalog
        assert isinstance(catalog, RacingCatalog)
        catalog.rival = lambda: store.push('sales', sales('rival'))
        store.push('sales', sales('mine'))
        assert sorted(store.read('sales')['key']) == ['mine', 'rival', 'seed']
        assert 'Attempt 1 of 4 failed' in caplog.text

    def test_a_key_both_writers_push_is_kept_once(self, store: IcebergFeatureStore) -> None:
        store.push('sales', sales('seed'))
        catalog = store.catalog
        assert isinstance(catalog, RacingCatalog)
        catalog.rival = lambda: store.push('sales', sales('same').assign(value=2.0))
        store.push('sales', sales('same').assign(value=3.0))
        assert store.read('sales', where={'key': 'same'})['value'].tolist() == [3.0]


class TestReplace:
    """A replace swaps the selected rows for new ones in one commit and leaves the other rows alone."""

    ROWS = pd.DataFrame(
        {
            'key': ['a@1', 'a@2', 'b@1'],
            'ticker': ['a', 'a', 'b'],
            'value': [1.0, 2.0, 3.0],
            'accepted_at': pd.to_datetime(['2026-11-19', '2026-11-20', '2026-11-19'], utc=True),
        }
    )

    def test_the_selected_rows_are_replaced_and_the_rest_kept(self, store: IcebergFeatureStore) -> None:
        store.push('sales', self.ROWS)
        versions = len(store.catalog.load_table('features.sales_v1').metadata.metadata_log)
        store.replace('sales', self.ROWS.iloc[[0]].assign(value=9.0), where={'ticker': ['a']})
        rows = store.read('sales', columns=('key', 'value')).sort_values('key')
        assert rows.to_dict('list') == {'key': ['a@1', 'b@1'], 'value': [9.0, 3.0]}
        # One commit: the table gained exactly one metadata version
        assert len(store.catalog.load_table('features.sales_v1').metadata.metadata_log) == versions + 1

    def test_empty_rows_delete_nothing(self, store: IcebergFeatureStore) -> None:
        store.push('sales', self.ROWS)
        store.replace('sales', self.ROWS.iloc[:0], where={'ticker': 'a'})
        assert len(store.read('sales')) == len(self.ROWS)
