"""Local-filesystem model store (a ``ModelStore`` adapter).

The model is a joblib file and its metadata a JSON file beside it
(``model.joblib`` + ``model.meta.json``), so the pair moves and versions together.
"""

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any


class LocalModelStore:
    """Save and load one model plus its metadata at a fixed path.

    Attributes:
        path: The model file.
        meta_path: The metadata file beside it.
    """

    def __init__(self, path: str | Path) -> None:
        """Bind the store to a model path.

        Args:
            path: Model file path; metadata is written next to it as ``<stem>.meta.json``.
        """
        self.path = Path(path)
        self.meta_path = self.path.with_suffix('.meta.json')

    def save(self, model: object, metadata: Mapping[str, Any]) -> None:
        """Write the model and its metadata, creating the directory if needed.

        Args:
            model: The fitted model to persist.
            metadata: JSON-serialisable facts about the model (non-JSON values are stringified).
        """
        # Lazy import so the dependency is only required when a model is stored.
        import joblib

        self.path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(model, self.path)
        self.meta_path.write_text(json.dumps(metadata, indent=2, default=str))

    def load(self) -> tuple[Any, dict[str, Any]]:
        """Read the model and its metadata.

        Returns:
            The model and its metadata dict.
        """
        import joblib

        return joblib.load(self.path), json.loads(self.meta_path.read_text())
