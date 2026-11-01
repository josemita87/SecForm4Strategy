"""Locations of the scraper's test data."""

from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / 'fixtures'

#: Real Form 4 submissions, each chosen for one parsing rule (docs/form4-parsing.md)
FOOTNOTE_10B5_1 = FIXTURES / '0000950103-23-004548.txt'  # META: 10b5-1 footnote on the shares, checkbox 0
JOINT_FILING = FIXTURES / '0001209191-23-028464.txt'  # PRME: seven reporting owners, checkbox 1
BOTH_HOLDINGS = FIXTURES / '0001104659-23-014571.txt'  # AMZN: direct and indirect holdings, footnote on the code
STOCK_AND_DERIVATIVE = FIXTURES / '0001127602-23-005881.txt'  # AMZN: option exercise, unreported stock price
OWNERSHIP_LINES = FIXTURES / '0001045810-23-000062.txt'  # NVDA: direct and indirect lines, several trade dates
