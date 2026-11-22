"""Locations of the site publisher's test data."""

from pathlib import Path

#: The site's sample data files, which the frontend renders without a deployed stack.
FIXTURES = Path(__file__).resolve().parents[2] / 'site' / 'fixtures'
