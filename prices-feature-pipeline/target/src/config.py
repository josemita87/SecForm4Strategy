"""Pydantic settings for the target microservice."""

from collections.abc import Callable
from pathlib import Path

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings

from src.constants import Direction


def _agg_dict() -> dict[str, str | Callable[..., object]]:
    """Resolve the aggregation policy from spec.yaml.

    Pandas aggregation names pass through unchanged; the ``mode_first`` token is
    mapped to ``lambda x: x.mode()[0]`` (most frequent value, first on ties).
    """
    ops = {'mode_first': lambda x: x.mode()[0]}
    spec = yaml.safe_load((Path(__file__).parent / 'spec.yaml').read_text())['agg_dict']
    return {col: ops.get(op, op) for col, op in spec.items()}


class Config(BaseSettings):
    """Configuration settings for the target microservice."""

    # Iceberg feature store; the catalog itself is configured by PYICEBERG_CATALOG__DEFAULT__* (docs/configuration.md)
    feature_store_namespace: str = Field('secform4strategy', json_schema_extra={'env': 'FEATURE_STORE_NAMESPACE'})
    feature_group_version: int = Field(..., json_schema_extra={'env': 'FEATURE_GROUP_VERSION'})

    # Investment settings
    delta_period: int = Field(..., json_schema_extra={'env': 'DELTA_PERIOD'})
    filter_key: str = Field(..., json_schema_extra={'env': 'FILTER_KEY'})
    acquired_disposed: Direction = Field(..., json_schema_extra={'env': 'ACQUIRED_DISPOSED'})

    # Reference data (docs/configuration.md)
    market_caps_path: Path = Field(
        default=Path(__file__).parent / 'data' / 'mcaps.parquet', json_schema_extra={'env': 'MARKET_CAPS_PATH'}
    )
    companies_path: Path = Field(
        default=Path(__file__).parent / 'data' / 'companies.json', json_schema_extra={'env': 'COMPANIES_PATH'}
    )

    # Aggregation policy, loaded and resolved from spec.yaml at instantiation
    agg_dict: dict[str, str | Callable[..., object]] = Field(default_factory=_agg_dict)

    # System settings
    system_training: bool = Field(False, json_schema_extra={'env': 'SYSTEM_TRAINING'})
    system_inference: bool = Field(False, json_schema_extra={'env': 'SYSTEM_INFERENCE'})
    delay: int = Field(0, json_schema_extra={'env': 'DELAY'})

    class Config:
        """Pydantic configuration controlling environment loading."""

        env_file = '.env'
        env_file_encoding = 'utf-8'


# Instantiate the configuration
config = Config()
