"""Filesystem locations of the kafka-to-store service, anchored on its source directory."""

from pathlib import Path

SPEC_PATH = Path(__file__).resolve().parent / 'spec.yaml'
