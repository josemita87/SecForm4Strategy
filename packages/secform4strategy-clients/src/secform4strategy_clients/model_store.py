"""Local-filesystem model store (a ``ModelStore`` adapter).

The model is a joblib file and its metadata a JSON file beside it
(``model.joblib`` + ``model.meta.json``), so the pair moves and versions together.
"""

from pathlib import Path
from typing import Any

import joblib
from pydantic import TypeAdapter

from secform4strategy_clients.ports import ModelMetadata

METADATA = TypeAdapter(ModelMetadata)


class LocalModelStore:
    """Save and load one model plus its metadata at a fixed path.

    Attributes:
        path: The model file.
        meta_path: The metadata file beside it.
    """

    def __init__(self, path: Path) -> None:
        """Bind the store to a model path.

        Args:
            path: Model file path; metadata is written next to it as ``<stem>.meta.json``.
        """
        self.path = path
        self.meta_path = path.with_suffix('.meta.json')

    def save(self, model: object, metadata: ModelMetadata) -> None:
        """Write the model and its metadata, creating the directory if needed.

        Args:
            model: The fitted model to persist.
            metadata: The facts about the model.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(model, self.path)
        self.meta_path.write_bytes(METADATA.dump_json(metadata, indent=2))

    def load(self) -> tuple[Any, ModelMetadata]:
        """Read the model and its validated metadata.

        Returns:
            The model and its metadata.
        """
        return joblib.load(self.path), METADATA.validate_json(self.meta_path.read_bytes())
