"""The site's sample data files match the records the publisher writes.

Under test:
    site/fixtures/*.json against site-publisher/src/records.py
"""

from typing import TYPE_CHECKING

import pytest
from constants import Section, file_name
from records import Explore, Health, Live, ModelReport, Portfolio

from tests.paths import FIXTURES

if TYPE_CHECKING:
    from records import DataFile


@pytest.mark.parametrize(
    ('section', 'record'),
    [
        (Section.LIVE, Live),
        (Section.HEALTH, Health),
        (Section.PORTFOLIO, Portfolio),
        (Section.MODEL, ModelReport),
        (Section.EXPLORE, Explore),
    ],
)
def test_fixture_validates(section: Section, record: type[DataFile]) -> None:
    record.model_validate_json((FIXTURES / file_name(section)).read_bytes())
