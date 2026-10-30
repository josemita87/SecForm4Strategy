"""Feature-group catalog: the validated ``feature_groups`` section of a service's ``spec.yaml``."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict


class FeatureGroupEntry(BaseModel):
    """One ``feature_groups`` entry as written in the YAML; unknown keys (``version`` included) are rejected."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    name: str | None = None
    primary_key: tuple[str, ...]
    event_time: str


class FeatureGroupSpec(FeatureGroupEntry):
    """A feature group as the store uses it: the entry with its name resolved and the version injected."""

    name: str
    version: int


def load_feature_group_catalog(spec_path: Path, version: int) -> dict[str, FeatureGroupSpec]:
    """Build a validated feature-group catalog from the ``feature_groups`` section of a YAML spec.

    ``version`` is injected so one template serves every feature-store version, and
    ``name`` defaults to the reference key unless the entry overrides it.

    Args:
        spec_path: Path to the service's YAML spec.
        version: Feature-group version injected into every entry.

    Returns:
        The validated specs, keyed by reference.
    """
    entries = {
        ref: FeatureGroupEntry.model_validate(raw)
        for ref, raw in yaml.safe_load(spec_path.read_text())['feature_groups'].items()
    }
    return {
        ref: FeatureGroupSpec.model_validate(entry.model_dump() | {'name': entry.name or ref, 'version': version})
        for ref, entry in entries.items()
    }
