"""Generic Hopsworks feature-store client.

Infra-only and domain-independent. The caller supplies a *catalog* mapping its
own references (e.g. ``"bt4"``) to feature-group specs at construction; the client
opens them and thereafter the caller pushes/reads by reference alone, never
needing to know a group's name, keys or event time. The client knows nothing
about what the references mean — the catalog is the caller's domain input.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any


def load_feature_group_catalog(spec_path: str | Path, version: int) -> dict[str, dict[str, Any]]:
    """Build a feature-group catalog from a YAML spec template.

    The YAML maps each reference (e.g. ``bi4``) to its spec fields
    (``primary_key``, ``event_time``, and any extra Hopsworks options). Two
    fields are supplied here rather than in the template: ``version`` is injected
    from ``version`` so one template serves every feature-store version, and
    ``name`` defaults to the reference key unless the entry overrides it.

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
        ref: {'name': spec.get('name', ref), 'version': version, **{k: v for k, v in spec.items() if k != 'name'}}
        for ref, spec in raw.items()
    }


class HopsworksClient:
    """Open a catalog of feature groups and push/read them by reference.

    Attributes:
        project: The authenticated Hopsworks project handle.
        fs: The project's feature store handle.
        write_options: Default write options used by ``push``.
    """

    def __init__(
        self,
        project_name: str,
        api_key: str,
        feature_groups: dict[str, dict],
        write_options: dict | None = None,
    ) -> None:
        """Log in, open the feature store, and resolve the feature-group catalog.

        Args:
            project_name: The Hopsworks project to connect to.
            api_key: The Hopsworks API key value.
            feature_groups: Maps a caller reference to the keyword arguments for
                ``get_or_create_feature_group`` (``name``, ``version``,
                ``primary_key``, ``event_time``, ...).
            write_options: Default write options for ``push``; defaults to
                ``{"start_offline_materialization": True, "mode": "append"}``.
        """
        # Lazy import: only pulled in when a client is actually constructed.
        import hopsworks

        self.project = hopsworks.login(project=project_name, api_key_value=api_key)
        self.fs = self.project.get_feature_store()
        self._fgs = {ref: self.fs.get_or_create_feature_group(**spec) for ref, spec in feature_groups.items()}
        self.write_options = (
            {'start_offline_materialization': True, 'mode': 'append'}
            if write_options is None
            else write_options
        )

    def push(self, reference: str, data, write_options: dict | None = None):
        """Insert a DataFrame into the referenced feature group, skipping empties.

        Args:
            reference: A key into the catalog given at construction.
            data: The records to insert (e.g. a pandas DataFrame).
            write_options: Overrides the client's default write options.

        Returns:
            The result of the underlying insert, or ``None`` if ``data`` is empty.
        """
        if hasattr(data, 'empty') and data.empty:
            return None
        options = self.write_options if write_options is None else write_options
        return self._fgs[reference].insert(features=data, write_options=options)

    def read(
        self,
        reference: str,
        where: Callable[[Any], Any] | None = None,
        read_options: dict | None = None,
    ):
        """Read the referenced feature group, optionally filtered.

        Args:
            reference: A key into the catalog given at construction.
            where: Optional callable that receives the feature group and returns a
                filtered query (e.g. ``lambda fg: fg.filter(fg['ticker'].isin(t))``).
                Keeps column knowledge in the caller; the client stays generic.
            read_options: Optional read options forwarded to Hopsworks.

        Returns:
            The (optionally filtered) contents as a DataFrame.
        """
        fg = self._fgs[reference]
        target = where(fg) if where is not None else fg
        return target.read(read_options=read_options or {})
