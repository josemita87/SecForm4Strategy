"""Generic Hopsworks feature-store client (a ``FeatureStore`` adapter).

Infra-only and domain-independent. The caller supplies a *catalog* mapping its
own references (e.g. ``"bt4"``) to feature-group specs at construction; the client
opens them and thereafter the caller pushes/reads by reference alone, never
needing to know a group's name, keys or event time. The client knows nothing
about what the references mean — the catalog is the caller's domain input.
"""

from __future__ import annotations

import functools
import operator
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from secform4strategy_clients.ports import is_many

if TYPE_CHECKING:
    from typing import Any

    import pandas as pd

    from secform4strategy_clients.ports import Filters


class FeatureGroupSpec(BaseModel):
    """One feature group's entry in a ``feature_groups.yaml`` catalog; unknown keys are rejected."""

    model_config = ConfigDict(extra='forbid')

    name: str | None = None
    primary_key: list[str]
    event_time: str
    online_enabled: bool = False
    stream: bool = False


def load_feature_group_catalog(spec_path: str | Path, version: int) -> dict[str, dict[str, Any]]:
    """Build a validated feature-group catalog from a YAML spec template.

    Each entry is validated against ``FeatureGroupSpec``. ``version`` is injected
    so one template serves every feature-store version, and ``name`` defaults to
    the reference key unless the entry overrides it. Options left out of the
    template are not passed, so Hopsworks keeps its own defaults.

    Args:
        spec_path: Path to the YAML catalog template.
        version: Feature-group version injected into every entry.

    Returns:
        A catalog dict suitable for :class:`HopsworksClient`, keyed by reference.
    """
    # Lazy import: only pulled in when a catalog is actually loaded.
    import yaml

    raw = yaml.safe_load(Path(spec_path).read_text()) or {}
    return {
        ref: {'name': ref, 'version': version} | FeatureGroupSpec.model_validate(spec).model_dump(exclude_unset=True)
        for ref, spec in raw.items()
    }


class HopsworksClient:
    """Open a catalog of feature groups and push/read them by reference.

    Attributes:
        project: The authenticated Hopsworks project handle.
        fs: The project's feature store handle.
        write_options: Write options used by ``push``.
        read_options: Read options used by ``read``.
    """

    def __init__(
        self,
        project_name: str,
        api_key: str,
        feature_groups: dict[str, dict[str, Any]],
        write_options: dict[str, Any] | None = None,
        read_options: dict[str, Any] | None = None,
    ) -> None:
        """Log in, open the feature store, and resolve the feature-group catalog.

        Args:
            project_name: The Hopsworks project to connect to.
            api_key: The Hopsworks API key value.
            feature_groups: Maps a caller reference to the keyword arguments for
                ``get_or_create_feature_group`` (``name``, ``version``,
                ``primary_key``, ``event_time``, ...).
            write_options: Write options for ``push``; defaults to
                ``{"start_offline_materialization": True, "mode": "append"}``.
            read_options: Read options for ``read``; defaults to ``{"use_hive": True}``.
        """
        # Lazy import: only pulled in when a client is actually constructed.
        import hopsworks

        self.project = hopsworks.login(project=project_name, api_key_value=api_key)
        self.fs = self.project.get_feature_store()
        self._fgs = {ref: self.fs.get_or_create_feature_group(**spec) for ref, spec in feature_groups.items()}
        self.write_options = write_options or {'start_offline_materialization': True, 'mode': 'append'}
        self.read_options = read_options or {'use_hive': True}

    def push(self, reference: str, data: pd.DataFrame) -> object:
        """Insert a DataFrame into the referenced feature group, skipping empties.

        Args:
            reference: A key into the catalog given at construction.
            data: The records to insert.

        Returns:
            The result of the underlying insert, or ``None`` if ``data`` is empty.
        """
        if data.empty:
            return None
        return self._fgs[reference].insert(features=data, write_options=self.write_options)

    def read(self, reference: str, where: Filters | None = None) -> pd.DataFrame:
        """Read the referenced feature group, optionally filtered.

        Args:
            reference: A key into the catalog given at construction.
            where: Column -> value equality filters; an iterable value matches any of its items.

        Returns:
            The (optionally filtered) contents as a DataFrame.
        """
        fg = self._fgs[reference]
        if where:
            fg = fg.filter(
                functools.reduce(
                    operator.and_,
                    [fg[col].isin(list(value)) if is_many(value) else fg[col] == value for col, value in where.items()],
                )
            )
        return fg.read(read_options=self.read_options)
