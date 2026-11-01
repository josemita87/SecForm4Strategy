"""Shared test setup for target: marks tests by directory and sets the config its modules need at import."""

import os

import pytest

os.environ.setdefault('FEATURE_GROUP_VERSION', '1')
os.environ.setdefault('DELTA_PERIOD', '30')
os.environ.setdefault('FILTER_KEY', 'acquired_disposed')
os.environ.setdefault('ACQUIRED_DISPOSED', 'D')


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark tests by their directory."""
    for item in items:
        if '/unit/' in str(item.path):
            item.add_marker(pytest.mark.unit)
