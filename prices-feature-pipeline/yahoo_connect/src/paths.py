"""Filesystem locations of the yahoo-connect service, anchored on its source directory."""

from pathlib import Path

SPEC_PATH = Path(__file__).resolve().parent / 'spec.yaml'
