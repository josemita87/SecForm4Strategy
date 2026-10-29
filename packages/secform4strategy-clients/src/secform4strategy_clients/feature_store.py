"""Feature store on Apache Iceberg (a ``FeatureStore`` adapter) and its feature-group catalog.

Infra-only and domain-independent. The caller supplies a *catalog* mapping its own
references (e.g. ``"bt4"``) to feature-group specs; thereafter it pushes and reads by
reference alone. The same code runs on a local SQLite catalog or on AWS (Glue or S3
Tables); only pyiceberg's catalog configuration changes.
"""

import functools
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from secform4strategy_clients.ports import is_many

if TYPE_CHECKING:
    from typing import Any

    import pandas as pd
    from pyiceberg.catalog import Catalog

    from secform4strategy_clients.ports import Filters


class FeatureGroupSpec(BaseModel):
    """One feature group's entry in a ``feature_groups.yaml`` catalog; unknown keys are rejected."""

    model_config = ConfigDict(extra='forbid')

    name: str | None = None
    primary_key: list[str]
    event_time: str


def load_feature_group_catalog(spec_path: str | Path, version: int) -> dict[str, dict[str, Any]]:
    """Build a validated feature-group catalog from a YAML spec template.

    Each entry is validated against ``FeatureGroupSpec``. ``version`` is injected
    so one template serves every feature-store version, and ``name`` defaults to
    the reference key unless the entry overrides it. Options left out of the
    template are not passed.

    Args:
        spec_path: Path to the YAML catalog template.
        version: Feature-group version injected into every entry.

    Returns:
        A catalog dict suitable for :class:`IcebergFeatureStore`, keyed by reference.
    """
    # Lazy import: only pulled in when a catalog is actually loaded.
    import yaml

    raw = yaml.safe_load(Path(spec_path).read_text()) or {}
    return {
        ref: {'name': ref, 'version': version} | FeatureGroupSpec.model_validate(spec).model_dump(exclude_unset=True)
        for ref, spec in raw.items()
    }


class IcebergFeatureStore:
    """Feature groups as Apache Iceberg tables (a ``FeatureStore`` adapter).

    Each catalog reference maps to the table ``<namespace>.<name>_v<version>``. A
    table is created by its first push and its schema is fixed then: later pushes
    are aligned to it (absent columns become null) and a new column needs a new
    feature-group version. Pushes upsert by ``primary_key``, replacing whole rows;
    within one push the last row per key wins.

    Attributes:
        catalog: The Iceberg catalog holding the tables.
        namespace: Namespace of the feature-group tables.
    """

    def __init__(self, catalog: Catalog, namespace: str, feature_groups: dict[str, dict[str, Any]]) -> None:
        """Bind the store to a catalog and a feature-group catalog, creating the namespace if needed.

        Args:
            catalog: Iceberg catalog, e.g. ``pyiceberg.catalog.load_catalog('default')``, configured
                through pyiceberg's ``PYICEBERG_CATALOG__DEFAULT__*`` environment variables.
            namespace: Namespace of the feature-group tables.
            feature_groups: Maps a caller reference to its spec (``name``, ``version``, ``primary_key``).
        """
        self.catalog = catalog
        self.namespace = namespace
        self._specs = feature_groups
        catalog.create_namespace_if_not_exists(namespace)

    def _identifier(self, reference: str) -> str:
        spec = self._specs[reference]
        return f'{self.namespace}.{spec["name"]}_v{spec["version"]}'

    def read(self, reference: str, where: Filters | None = None) -> pd.DataFrame:
        """Read the referenced feature group, optionally filtered.

        Args:
            reference: A key into the catalog given at construction.
            where: Column -> value equality filters; an iterable value matches any of its items.

        Returns:
            The (optionally filtered) rows; a missing table raises ``NoSuchTableError``.
        """
        from pyiceberg.expressions import AlwaysTrue, And, EqualTo, In

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
        import pyarrow as pa
        from pyiceberg.exceptions import NoSuchTableError
        from pyiceberg.io.pyarrow import schema_to_pyarrow

        if data.empty:
            return None
        keys = self._specs[reference]['primary_key']
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
