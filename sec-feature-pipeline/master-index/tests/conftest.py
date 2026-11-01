"""Shared test setup for master-index: marks tests by directory and sets the config its modules need at import."""

import os

import pytest

os.environ.setdefault('KAFKA_BROKER_ADDRESS', 'test:9092')
os.environ.setdefault('KAFKA_OUTPUT_TOPIC', 'filings')
os.environ.setdefault('SEC_USER_AGENT', 'Test Suite test@example.com')
os.environ.setdefault('BUFFER_SIZE', '10')
os.environ.setdefault('MODE', 'live')


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark tests by their directory."""
    for item in items:
        if '/unit/' in str(item.path):
            item.add_marker(pytest.mark.unit)
