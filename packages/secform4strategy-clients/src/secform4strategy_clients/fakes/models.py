"""An in-memory ``ModelStore``."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from secform4strategy_clients.model_store import version_of
from secform4strategy_clients.records import ModelMetadata

if TYPE_CHECKING:
    from secform4strategy_clients.ports import ModelStore


@dataclass
class FakeModelStore:
    """Saved models and their metadata by version, and the promoted version.

    Attributes:
        saved: The model and metadata of each saved version.
        promoted: The promoted version, or None.
        promotions: Every version promoted, in order.
    """

    saved: dict[str, tuple[Any, ModelMetadata]] = field(default_factory=dict)
    promoted: str | None = None
    promotions: list[str] = field(default_factory=list)

    def save(self, model: object, metadata: ModelMetadata) -> str:
        """Record the model under its version and return the version."""
        self.saved[version := version_of(metadata)] = (model, metadata)
        return version

    def promote(self, version: str) -> None:
        """Promote a saved version; an unsaved one is a ``KeyError``."""
        if version not in self.saved:
            raise KeyError(version)
        self.promoted = version
        self.promotions.append(version)

    def load(self) -> tuple[Any, ModelMetadata]:
        """Return the promoted model and its metadata; none promoted is a ``KeyError``."""
        return self.saved[str(self.promoted)]

    def latest_version(self) -> str | None:
        """Return the promoted version."""
        return self.promoted

    def metadata(self, version: str) -> ModelMetadata:
        """Return a saved version's metadata."""
        return self.saved[version][1]


_conforms: ModelStore = FakeModelStore()
