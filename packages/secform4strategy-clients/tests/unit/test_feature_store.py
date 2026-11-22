"""Recent rows come from an Iceberg table by event time; a table not created yet has none.

Under test:
    packages/secform4strategy-clients/src/secform4strategy_clients/feature_store.py
"""

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest
from pyiceberg.catalog.sql import SqlCatalog
from secform4strategy_clients.feature_groups import FeatureGroupSpec
from secform4strategy_clients.feature_store import IcebergFeatureStore

SINCE = datetime(2026, 11, 20, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> IcebergFeatureStore:
    """A feature store on a SQLite catalog in a temporary directory, with one feature group."""
    catalog = SqlCatalog('test', uri=f'sqlite:///{tmp_path}/catalog.db', warehouse=tmp_path.as_uri())
    spec = FeatureGroupSpec(name='sales', version=1, primary_key=('key',), event_time='accepted_at')
    return IcebergFeatureStore(catalog, 'features', {'sales': spec})


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
