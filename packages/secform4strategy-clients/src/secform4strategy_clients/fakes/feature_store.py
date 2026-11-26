"""Fake of the ``FeatureStore`` port (needs the ``feature-store`` extra for pandas and pyiceberg's errors)."""

import functools
import operator
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pandas as pd
from pyiceberg.exceptions import NoSuchTableError

from secform4strategy_clients.ports import Filters, RetryableError, is_many

if TYPE_CHECKING:
    from secform4strategy_clients.ports import FeatureStore


@dataclass
class FakeFeatureStore:
    """Tables as DataFrames by reference, upserted by one key column as the Iceberg store does.

    Attributes:
        tables: The rows of each reference.
        key: The key column every table upserts by.
        conflicts: How many more pushes fail with ``RetryableError``, as a store whose commit retries ran out.
        pushes: Every push that landed, as (reference, rows).
        replaces: Every ``replace`` that landed, as (reference, where).
    """

    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    key: str = 'key'
    conflicts: int = 0
    pushes: list[tuple[str, pd.DataFrame]] = field(default_factory=list)
    replaces: list[tuple[str, Filters]] = field(default_factory=list)

    def _selected(self, reference: str, where: Filters) -> pd.Series:
        """Return the mask of the rows ``where`` selects."""
        rows = self.tables[reference]
        return functools.reduce(
            operator.and_,
            (rows[col].isin(list(value)) if is_many(value) else rows[col] == value for col, value in where.items()),
            pd.Series(True, index=rows.index),
        )

    def read(self, reference: str, where: Filters | None = None, columns: Iterable[str] | None = None) -> pd.DataFrame:
        """Return the matching rows; a missing table raises ``NoSuchTableError``, as Iceberg does."""
        if reference not in self.tables:
            raise NoSuchTableError(reference)
        rows = self.tables[reference][self._selected(reference, where or {})]
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

    def replace(self, reference: str, data: pd.DataFrame, where: Filters) -> None:
        """Delete the selected rows and append ``data`` in their place; skips empties."""
        if data.empty:
            return
        self.replaces.append((reference, where))
        self.tables[reference] = pd.concat(
            [self.tables[reference][~self._selected(reference, where)], data], ignore_index=True
        )


_conforms: FeatureStore = FakeFeatureStore()
