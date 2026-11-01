"""Filesystem locations of the target service, anchored on its source directory; config fields default to them."""

from pathlib import Path

SRC = Path(__file__).resolve().parent

SPEC_PATH = SRC / 'spec.yaml'
MARKET_CAPS_PATH = SRC / 'data' / 'mcaps.parquet'
COMPANIES_PATH = SRC / 'data' / 'companies.json'
