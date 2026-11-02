"""Feature store on Apache Iceberg (a ``FeatureStore`` adapter).

Infra-only and domain-independent. The caller supplies a catalog mapping its own
references (e.g. ``"bt4"``) to feature-group specs (``feature_groups``); thereafter it pushes and reads by
reference alone. The same code runs on a local SQLite catalog or on AWS (Glue or S3
Tables); only pyiceberg's catalog configuration changes.
"""

import functools
from collections.abc import Mapping
from typing import TYPE_CHECKING

import pyarrow as pa
from pyiceberg.exceptions import NoSuchTableError
from pyiceberg.expressions import AlwaysTrue, And, EqualTo, In
from pyiceberg.io.pyarrow import schema_to_pyarrow

from secform4strategy_clients.ports import is_many

if TYPE_CHECKING:
    import pandas as pd
    from pyiceberg.catalog import Catalog

    from secform4strategy_clients.feature_groups import FeatureGroupSpec
    from secform4strategy_clients.ports import Filters


class IcebergFeatureStore:
    """Feature groups as Apache Iceberg tables (a ``FeatureStore`` adapter).

    Each catalog reference maps to the table ``<namespace>.<name>_v<version>``. A
    table is created by its first push and its schema is fixed then: later pushes
    are aligned to it (absent columns become null) and a new column needs a new
    feature-group version. Pushes upsert by ``primary_key``, replacing whole rows;
    within one push the last row per key wins. Keys are one column: pyiceberg 0.12's upsert
    crashes the process on a composite key, so a composite identity is joined into one string.

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

    def read(self, reference: str, where: Filters | None = None) -> pd.DataFrame:
        """Read the referenced feature group, optionally filtered.

        Args:
            reference: A key into the catalog given at construction.
            where: Column -> value equality filters; an iterable value matches any of its items.

        Returns:
            The (optionally filtered) rows; a missing table raises ``NoSuchTableError``.
        """
        conditions = [
            In(col, list(value)) if is_many(value) else EqualTo(col, value) for col, value in (where or {}).items()
        ]
        row_filter = functools.reduce(And, conditions) if conditions else AlwaysTrue()
        data: pd.DataFrame = (
            self.catalog.load_table(self._identifier(reference)).scan(row_filter=row_filter).to_pandas()
        )
        return data

    def push(self, reference: str, data: pd.DataFrame) -> object:
        """Upsert a DataFrame into the referenced feature group by its primary key, skipping empties.

        Args:
            reference: A key into the catalog given at construction.
            data: The rows to upsert.

        Returns:
            The upsert result (rows inserted / updated), or ``None`` if ``data`` is empty.

        Raises:
            ValueError: If ``data`` has columns the table does not (bump the feature-group version).
        """
        if data.empty:
            return None
        keys = list(self._specs[reference].primary_key)
        incoming = pa.Table.from_pandas(data.drop_duplicates(subset=keys, keep='last'), preserve_index=False)
        # Iceberg has no dictionary type and stores timestamps in microseconds
        incoming = incoming.cast(
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
        identifier = self._identifier(reference)
        try:
            table = self.catalog.load_table(identifier)
        except NoSuchTableError:
            table = self.catalog.create_table(identifier, schema=incoming.schema)

        target = schema_to_pyarrow(table.schema())
        if unknown := sorted(set(incoming.column_names) - set(target.names)):
            raise ValueError(f'{identifier} has no columns {unknown}; bump the feature-group version for a new schema')
        for field in target:
            if field.name not in incoming.column_names:
                incoming = incoming.append_column(field.name, pa.nulls(len(incoming), field.type))
        return table.upsert(incoming.select(target.names).cast(target), join_cols=keys)
