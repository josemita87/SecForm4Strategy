"""Models are saved to versioned local folders, and promoting one copies it to the path loads read.

Under test:
    packages/secform4strategy-clients/src/secform4strategy_clients/model_store.py
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from secform4strategy_clients.model_store import LocalModelStore
from secform4strategy_clients.records import ModelMetadata, Scores

METADATA_RECORD = ModelMetadata(
    trained_at=datetime(2026, 11, 13, 6, 0, tzinfo=UTC),
    git_sha='abc123',
    sklearn_version='1.7.0',
    feature_group_version=1,
    target='return_5d',
    features=('a', 'b'),
    model_params={'alpha': 0.5},
    train_rows=100,
    test_rows=20,
    test_start=datetime(2026, 10, 1, tzinfo=UTC),
    metrics={'model': Scores(mae=0.1, short_return=0.02, n_shorts=5)},
)


@pytest.fixture
def store(tmp_path: Path) -> LocalModelStore:
    """A store whose promoted model sits at ``models/model.joblib`` in a temporary directory."""
    return LocalModelStore(tmp_path / 'models' / 'model.joblib')


class TestSave:
    """The pair goes to a version folder, and nothing is promoted."""

    def test_the_pair_is_written_to_the_version_folder(self, store: LocalModelStore) -> None:
        assert store.save({'weights': [1, 2]}, METADATA_RECORD) == '20261113T060000Z'
        assert store.metadata('20261113T060000Z') == METADATA_RECORD
        assert (store.path.parent / '20261113T060000Z' / 'model.joblib').exists()

    def test_saving_promotes_nothing(self, store: LocalModelStore) -> None:
        store.save({'weights': [1, 2]}, METADATA_RECORD)
        assert store.latest_version() is None
        assert not store.path.exists()


class TestPromote:
    """Promoting a version makes it the one loaded."""

    def test_the_promoted_version_is_loaded(self, store: LocalModelStore) -> None:
        store.promote(store.save({'weights': [1, 2]}, METADATA_RECORD))
        assert store.latest_version() == '20261113T060000Z'
        assert store.load() == ({'weights': [1, 2]}, METADATA_RECORD)

    def test_an_unsaved_version_cannot_be_promoted(self, store: LocalModelStore) -> None:
        store.path.parent.mkdir(parents=True)
        with pytest.raises(FileNotFoundError):
            store.promote('20991231T000000Z')
        assert store.latest_version() is None
