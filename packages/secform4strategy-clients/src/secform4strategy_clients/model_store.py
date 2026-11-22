"""Local-filesystem model store (a ``ModelStore`` adapter).

The model is a joblib file and its metadata a JSON file beside it
(``model.joblib`` + ``model.meta.json``), so the pair moves and versions together.
Each save writes the pair to a folder named after the training time, beside the store's path;
promoting a version copies its pair to the store's path, which ``load`` reads.
"""

import shutil
from datetime import UTC
from pathlib import Path
from typing import Any

import joblib
from pydantic import TypeAdapter

from secform4strategy_clients.records import ModelMetadata

METADATA = TypeAdapter(ModelMetadata)


def version_of(metadata: ModelMetadata) -> str:
    """Return the version a model is saved under: its training time in UTC.

    Args:
        metadata: The facts about the model.

    Returns:
        The version, such as ``20261120T210617Z``.
    """
    return metadata.trained_at.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')


class LocalModelStore:
    """Save versioned models plus their metadata beside a fixed path, and load the promoted one from it.

    Attributes:
        path: The promoted model file.
        meta_path: The promoted metadata file beside it.
    """

    def __init__(self, path: Path) -> None:
        """Bind the store to a model path.

        Args:
            path: Promoted model file path; metadata sits next to it as ``<stem>.meta.json``.
        """
        self.path = path
        self.meta_path = path.with_suffix('.meta.json')

    def save(self, model: object, metadata: ModelMetadata) -> str:
        """Write the model and its metadata to a new version folder beside the path, without promoting it.

        Args:
            model: The fitted model to persist.
            metadata: The facts about the model; ``trained_at`` names the version.

        Returns:
            The version.
        """
        version = version_of(metadata)
        model_path, meta_path = self._pair(version)
        model_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(model, model_path)
        meta_path.write_bytes(METADATA.dump_json(metadata, indent=2))
        return version

    def promote(self, version: str) -> None:
        """Copy a saved version's model and metadata to the path ``load`` reads.

        Args:
            version: The version folder.
        """
        model_path, meta_path = self._pair(version)
        shutil.copyfile(model_path, self.path)
        shutil.copyfile(meta_path, self.meta_path)

    def load(self) -> tuple[Any, ModelMetadata]:
        """Read the promoted model and its validated metadata.

        Returns:
            The model and its metadata.
        """
        return joblib.load(self.path), METADATA.validate_json(self.meta_path.read_bytes())

    def latest_version(self) -> str | None:
        """Return the promoted version, or None when no model was promoted.

        Returns:
            The version.
        """
        return version_of(METADATA.validate_json(self.meta_path.read_bytes())) if self.meta_path.exists() else None

    def metadata(self, version: str) -> ModelMetadata:
        """Return a saved version's validated metadata, without its model.

        Args:
            version: The version folder.

        Returns:
            The metadata.
        """
        return METADATA.validate_json(self._pair(version)[1].read_bytes())

    def _pair(self, version: str) -> tuple[Path, Path]:
        """Return a version's model and metadata files."""
        folder = self.path.parent / version
        return folder / self.path.name, folder / self.meta_path.name
