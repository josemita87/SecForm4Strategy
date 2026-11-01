"""Shared test setup for yahoo_connect: marks tests by directory."""

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark tests by their directory."""
    for item in items:
        if '/unit/' in str(item.path):
            item.add_marker(pytest.mark.unit)
