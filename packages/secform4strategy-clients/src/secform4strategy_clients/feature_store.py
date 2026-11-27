"""Feature store on Apache Iceberg (a ``FeatureStore`` and ``RecentRows`` adapter).

Infra-only and domain-independent. The caller supplies a catalog mapping its own
references (e.g. ``"bt4"``) to feature-group specs (``feature_groups``); thereafter it pushes and reads by
reference alone. The same code runs on a local SQLite catalog or on AWS (Glue or S3
Tables); only pyiceberg's catalog configuration changes.
"""

import functools
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd
import pyarrow as pa
from pyiceberg.exceptions import CommitFailedException, NoSuchTableError, ValidationException
from pyiceberg.expressions import AlwaysTrue, And, EqualTo, GreaterThanOrEqual, In
from pyiceberg.io.pyarrow import schema_to_pyarrow
from pyiceberg.table import TableProperties

from secform4strategy_clients.ports import RetryableError, is_many
from secform4strategy_clients.retry import with_backoff

if TYPE_CHECKING:
    from pyiceberg.catalog import Catalog
    from pyiceberg.expressions import BooleanExpression
    from pyiceberg.table import Table

    from secform4strategy_clients.feature_groups import FeatureGroupSpec
    from secform4strategy_clients.ports import Filters


class IcebergFeatureStore:
    """Feature groups as Apache Iceberg tables (a ``FeatureStore`` and ``RecentRows`` adapter).

    Each catalog reference maps to the table ``<namespace>.<name>_v<version>``. A
    table is created by its first push and its schema is fixed then: later pushes
    are aligned to it (absent columns become null) and a new column needs a new
    feature-group version. Pushes upsert by ``primary_key``, replacing whole rows;
    within one push the last row per key wins. Keys are one column: pyiceberg 0.12's upsert
    crashes the process on a composite key, so a composite identity is joined into one string.
    A push that loses a commit race reloads the table and upserts again. Before its first write to a
    table, the store sets the spec's ``properties`` and turns off pyiceberg's own commit retry, which
    re-appends the rows without matching them against the rival's commit and so can duplicate a key.

    Attributes:
        catalog: The Iceberg catalog holding the tables.
        namespace: Namespace of the feature-group tables.
    """

    def __init__(self, catalog: Catalog, namespace: str, feature_groups: Mapping[str, FeatureGroupSpec]) -> None:
        """Bind the store to a catalog and a feature-group catalog, creating the namespace if needed.

        Args:
            catalog: Iceberg catalog, e.g. ``pyiceberg.catalog.load_catalog('default')``, configured
                through pyiceberg's ``PYICEBERG_CATALOG__DEFAULT__*`` environment variables.
            namespace: Namespace of the feature-group tables.
            feature_groups: Maps a caller reference to its feature-group spec.
        """
        self.catalog = catalog
        self.namespace = namespace
        self._specs = feature_groups
        catalog.create_namespace_if_not_exists(namespace)

    def _identifier(self, reference: str) -> str:
        """Return the table identifier ``<namespace>.<name>_v<version>`` of a reference."""
        return f'{self.namespace}.{(spec := self._specs[reference]).name}_v{spec.version}'

    def read(self, reference: str, where: Filters | None = None, columns: Iterable[str] | None = None) -> pd.DataFrame:
        """Read the referenced feature group, optionally filtered and with only some columns.

        Iceberg skips the data files the filters exclude and the columns not selected, so a narrow read
        holds only what the caller needs in memory.

        Args:
            reference: A key into the catalog given at construction.
            where: Column -> value equality filters; an iterable value matches any of its items.
            columns: The columns to read; None reads every column.

        Returns:
            The (optionally filtered) rows; a missing table raises ``NoSuchTableError``.
        """
        data: pd.DataFrame = (
            self.catalog.load_table(self._identifier(reference))
            .scan(row_filter=_row_filter(where or {}), selected_fields=('*',) if columns is None else tuple(columns))
            .to_pandas()
        )
        return data

    def read_since(self, reference: str, since: datetime, columns: Iterable[str]) -> pd.DataFrame:
        """Read the rows whose event time is at or after ``since``, with only ``columns`` (a ``RecentRows`` read).

        Iceberg skips the data files outside the window and the columns not selected.

        Args:
            reference: A key into the catalog given at construction.
            since: The earliest event time included.
            columns: The columns to read.

        Returns:
            The rows; no rows with the requested columns when the table does not exist yet.
        """
        try:
            table = self.catalog.load_table(self._identifier(reference))
        except NoSuchTableError:
            return pd.DataFrame(columns=list(columns))
        data: pd.DataFrame = table.scan(
            row_filter=GreaterThanOrEqual(self._specs[reference].event_time, since.isoformat()),
            selected_fields=tuple(columns),
        ).to_pandas()
        return data

    def push(self, reference: str, data: pd.DataFrame) -> object:
        """Upsert a DataFrame into the referenced feature group by its primary key, skipping empties.

        Args:
            reference: A key into the catalog given at construction.
            data: The rows to upsert.

        Returns:
            The upsert result (rows inserted / updated), or ``None`` if ``data`` is empty.
        """
        if data.empty:
            return None
        keys = list(self._specs[reference].primary_key)
        incoming = _to_iceberg_types(data.drop_duplicates(subset=keys, keep='last'))
        return with_backoff(lambda: self._upsert_once(reference, incoming, keys))

    def replace(self, reference: str, data: pd.DataFrame, where: Filters) -> None:
        """Delete the rows ``where`` selects and append ``data`` in their place, in one commit; skips empties.

        Iceberg prunes the data files by ``where`` (column statistics), so the delete rewrites only the
        files holding selected rows. Each ``In`` filter must stay at most 200 values: pyiceberg stops
        pruning above that and the delete reads every file.

        Args:
            reference: A key into the catalog given at construction.
            data: The rows that replace the selected ones.
            where: Column -> value equality filters selecting the rows to replace; an iterable matches any item.
        """
        if data.empty:
            return
        incoming = _to_iceberg_types(data.drop_duplicates(subset=list(self._specs[reference].primary_key), keep='last'))
        with_backoff(lambda: self._replace_once(reference, incoming, _row_filter(where)))

    def _writable(self, reference: str, schema: pa.Schema) -> Table:
        """Load the table at its latest snapshot, or create it with ``schema``, with its properties set.

        Args:
            reference: A key into the catalog given at construction.
            schema: The schema a new table gets.

        Returns:
            The table, its commit retry off and the spec's ``properties`` set (one commit the first time).
        """
        identifier = self._identifier(reference)
        properties = {TableProperties.COMMIT_NUM_RETRIES: '0'} | self._specs[reference].properties
        try:
            table = self.catalog.load_table(identifier)
        except NoSuchTableError:
            # A rival that creates the table first wins; this writer loads it instead of failing
            return self.catalog.create_table_if_not_exists(identifier, schema=schema, properties=properties)
        if missing := {key: value for key, value in properties.items() if table.properties.get(key) != value}:
            with table.transaction() as transaction:
                transaction.set_properties(missing)
        return table

    def _replace_once(self, reference: str, incoming: pa.Table, row_filter: BooleanExpression) -> None:
        """Load the table at its latest snapshot and replace the rows ``row_filter`` selects once.

        Args:
            reference: A key into the catalog given at construction.
            incoming: The rows, deduplicated by key, with Iceberg's column types.
            row_filter: The rows to delete.

        Raises:
            RetryableError: Another writer committed first, or changed the selected rows; the next attempt reloads.
        """
        try:
            table = self._writable(reference, incoming.schema)
            table.overwrite(_aligned(incoming, table, self._identifier(reference)), overwrite_filter=row_filter)
        except (CommitFailedException, ValidationException) as error:
            raise RetryableError(f'Replace in {self._identifier(reference)}: {error}') from error

    def _upsert_once(self, reference: str, incoming: pa.Table, keys: list[str]) -> object:
        """Load (or create) the table at its latest snapshot and upsert into it once.

        Args:
            reference: A key into the catalog given at construction.
            incoming: The rows, deduplicated by key, with Iceberg's column types.
            keys: The primary-key columns.

        Returns:
            The upsert result (rows inserted / updated).

        Raises:
            RetryableError: Another writer committed first, or rewrote the files the upsert replaces (compaction);
                the next attempt reloads the table.
        """
        try:
            table = self._writable(reference, incoming.schema)
            return table.upsert(_aligned(incoming, table, self._identifier(reference)), join_cols=keys)
        except (CommitFailedException, ValidationException) as error:
            raise RetryableError(f'Upsert into {self._identifier(reference)}: {error}') from error


def _row_filter(where: Filters) -> BooleanExpression:
    """Return the Iceberg expression of column -> value equality filters; an iterable value matches any item."""
    conditions = [In(col, list(value)) if is_many(value) else EqualTo(col, value) for col, value in where.items()]
    return functools.reduce(And, conditions) if conditions else AlwaysTrue()


def _to_iceberg_types(data: pd.DataFrame) -> pa.Table:
    """Return the rows as Arrow with Iceberg's types: no dictionaries, timestamps in microseconds."""
    incoming = pa.Table.from_pandas(data, preserve_index=False)
    return incoming.cast(
        pa.schema(
            pa.field(
                field.name,
                field.type.value_type
                if pa.types.is_dictionary(field.type)
                else pa.timestamp('us', field.type.tz)
                if pa.types.is_timestamp(field.type)
                else field.type,
            )
            for field in incoming.schema
        )
    )


def _aligned(incoming: pa.Table, table: Table, identifier: str) -> pa.Table:
    """Return ``incoming`` in the table's column order and types, absent columns as nulls.

    Args:
        incoming: The rows, with Iceberg's column types.
        table: The table the rows go into.
        identifier: The table's identifier, for the error message.

    Returns:
        The rows, cast to the table's Arrow schema.

    Raises:
        ValueError: If ``incoming`` has columns the table does not (bump the feature-group version).
    """
    target = schema_to_pyarrow(table.schema())
    if unknown := sorted(set(incoming.column_names) - set(target.names)):
        raise ValueError(f'{identifier} has no columns {unknown}; bump the feature-group version for a new schema')
    return pa.table(
        {
            field.name: incoming[field.name]
            if field.name in incoming.column_names
            else pa.nulls(len(incoming), field.type)
            for field in target
        }
    ).cast(target)
