"""A saved model is promoted only under the gated policy, and only when every gate check passes.

Under test:
    training/src/promotion.py
"""

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from promotion import PromotionSpec, decide, gate
from secform4strategy_clients.fakes.models import FakeModelStore
from secform4strategy_clients.records import ModelMetadata, Scores

if TYPE_CHECKING:
    from promotion import Check

SPEC = PromotionSpec(min_train_rows=1000, min_rows_ratio=0.8)
CURRENT = ModelMetadata(
    trained_at=datetime(2026, 11, 14, 6, 0, tzinfo=UTC),
    git_sha='abc123',
    sklearn_version='1.9.1',
    feature_group_version=1,
    target='pct_change',
    features=('a', 'b'),
    model_params={'learning_rate': 0.1},
    train_rows=2000,
    test_rows=500,
    test_start=datetime(2026, 9, 1, tzinfo=UTC),
    metrics={'model': Scores(short_return=0.1, n_shorts=30, average_precision=0.35)},
    probability_cutoff=0.55,
)
NEW = replace(CURRENT, trained_at=datetime(2026, 11, 21, 6, 0, tzinfo=UTC), train_rows=2100)
#: The promoted regressor, as its saved metadata loads: no cutoff and no average precision
REGRESSOR = replace(CURRENT, metrics={'model': Scores(short_return=0.03, n_shorts=600)}, probability_cutoff=None)


def check(new: ModelMetadata, current: ModelMetadata | None, name: str) -> Check:
    """Return the named check of the gate's verdict."""
    return next(check for check in gate(new, current, SPEC).checks if check.name == name)


class TestGate:
    """Each check passes on a sound model and fails on its own fault."""

    def test_a_sound_model_passes_every_check(self) -> None:
        verdict = gate(NEW, CURRENT, SPEC)
        assert verdict.promote
        assert {check.name for check in verdict.checks} == {
            'min_train_rows',
            'train_rows_vs_current',
            'feature_group_version',
            'features',
            'test_rows',
            'holdout_average_precision',
            'n_shorts',
        }

    def test_the_first_model_skips_the_comparisons_with_a_current_one(self) -> None:
        verdict = gate(NEW, None, SPEC)
        assert verdict.promote
        assert [check.name for check in verdict.checks] == [
            'min_train_rows',
            'test_rows',
            'holdout_average_precision',
            'n_shorts',
        ]

    def test_the_first_model_still_needs_enough_rows(self) -> None:
        assert not gate(replace(NEW, train_rows=999), None, SPEC).promote

    @pytest.mark.parametrize(
        ('fault', 'name'),
        [
            ({'train_rows': 999}, 'min_train_rows'),
            ({'train_rows': 1599}, 'train_rows_vs_current'),
            ({'feature_group_version': 2}, 'feature_group_version'),
            ({'features': ('b', 'a')}, 'features'),
            ({'features': ('a', 'b', 'c')}, 'features'),
            ({'test_rows': 0}, 'test_rows'),
            (
                {'metrics': {'model': Scores(short_return=0.1, n_shorts=30, average_precision=float('nan'))}},
                'holdout_average_precision',
            ),
            ({'metrics': {'model': Scores(short_return=0.1, n_shorts=30)}}, 'holdout_average_precision'),
            ({'metrics': {}}, 'holdout_average_precision'),
            ({'metrics': {'model': Scores(short_return=0.0, n_shorts=0, average_precision=0.35)}}, 'n_shorts'),
            ({'metrics': {}}, 'n_shorts'),
        ],
    )
    def test_a_fault_fails_its_check_and_the_gate(self, fault: dict[str, object], name: str) -> None:
        faulty = replace(NEW, **fault)
        assert not check(faulty, CURRENT, name).passed
        assert not gate(faulty, CURRENT, SPEC).promote

    def test_the_row_ratio_boundary_passes(self) -> None:
        assert check(replace(NEW, train_rows=1600), CURRENT, 'train_rows_vs_current').passed

    def test_a_worse_holdout_than_the_current_model_still_passes(self) -> None:
        worse = replace(NEW, metrics={'model': Scores(short_return=-0.1, n_shorts=3, average_precision=0.1)})
        assert gate(worse, CURRENT, SPEC).promote

    def test_a_classifier_compares_with_a_promoted_regressor(self) -> None:
        assert gate(NEW, REGRESSOR, SPEC).promote
        assert not check(replace(NEW, train_rows=1599), REGRESSOR, 'train_rows_vs_current').passed


class TestDecide:
    """The policy picks whether the gate runs; a passing gate promotes the saved version."""

    @pytest.fixture
    def store(self) -> FakeModelStore:
        """A store holding the current model, promoted."""
        models = FakeModelStore()
        models.promote(models.save('current', CURRENT))
        return models

    def test_never_saves_without_promoting(self, store: FakeModelStore) -> None:
        version = store.save('new', NEW)
        assert decide(store, version, NEW, 'never', SPEC) is None
        assert store.latest_version() == '20261114T060000Z'

    def test_gated_promotes_a_passing_model(self, store: FakeModelStore) -> None:
        version = store.save('new', NEW)
        verdict = decide(store, version, NEW, 'gated', SPEC)
        assert verdict is not None
        assert verdict.promote
        assert store.load() == ('new', NEW)

    def test_gated_keeps_the_current_model_when_a_check_fails(self, store: FakeModelStore) -> None:
        truncated = replace(NEW, train_rows=1200)
        version = store.save('new', truncated)
        verdict = decide(store, version, truncated, 'gated', SPEC)
        assert verdict is not None
        assert not verdict.promote
        assert store.promotions == ['20261114T060000Z']

    def test_gated_promotes_the_first_model(self) -> None:
        store = FakeModelStore()
        version = store.save('new', NEW)
        assert decide(store, version, NEW, 'gated', SPEC) is not None
        assert store.latest_version() == version
