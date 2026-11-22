"""Filesystem locations of the site publisher, anchored on its directory; config fields default to them."""

from pathlib import Path

SPEC_PATH = Path(__file__).resolve().parent / 'spec.yaml'
