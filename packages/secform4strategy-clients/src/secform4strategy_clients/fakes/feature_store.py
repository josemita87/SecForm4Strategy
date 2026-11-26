"""Fake of the ``FeatureStore`` port (needs the ``feature-store`` extra for pandas)."""

import functools
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pandas as pd

from secform4strategy_clients.ports import RetryableError, is_many

if TYPE_CHECKING:
    from secform4strategy_clients.ports import FeatureStore, Filters


@dataclass
class FakeFeatureStore:
    """Tables as DataFrames by reference, upserted by one key column as the Iceberg store does.

    Attributes:
        tables: The rows of each reference.
        key: The key column every table upserts by.
        conflicts: How many more pushes fail with ``RetryableError``, as a store whose commit retries ran out.
        pushes: Every push that landed, as (reference, rows).
    """

    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    key: str = 'key'
    conflicts: int = 0
    pushes: list[tuple[str, pd.DataFrame]] = field(default_factory=list)

    def read(self, reference: str, where: Filters | None = None, columns: Iterable[str] | None = None) -> pd.DataFrame:
        """Return the matching rows; a missing table raises ``KeyError``, not ``NoSuchTableError``."""
        rows = self.tables[reference]
        masks = [
            rows[col].isin(list(value)) if is_many(value) else rows[col] == value
            for col, value in (where or {}).items()
        ]
        if masks:
            rows = rows[functools.reduce(lambda left, right: left & right, masks)]
        return (rows if columns is None else rows[list(columns)]).reset_index(drop=True)

    def push(self, reference: str, data: pd.DataFrame) -> object:
        """Upsert the rows by key, the last row per key winning; fail while ``conflicts`` remain."""
        if data.empty:
            return None
        if self.conflicts:
            self.conflicts -= 1
            raise RetryableError(f'Upsert into {reference}: commit conflict')
        self.pushes.append((reference, data))
        old = self.tables.get(reference, data.iloc[:0])
        self.tables[reference] = pd.concat(
            [old[~old[self.key].isin(data[self.key])], data.drop_duplicates(self.key, keep='last')], ignore_index=True
        )
        return len(data)


_conforms: FeatureStore = FakeFeatureStore()
