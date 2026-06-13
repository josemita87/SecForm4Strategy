"""Pydantic settings for the target microservice."""

from pathlib import Path

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings


def _agg_dict() -> dict:
    """Resolve the aggregation policy from specs.yaml.

    Pandas aggregation names pass through unchanged; the ``mode_first`` token is
    mapped to ``lambda x: x.mode()[0]`` (most frequent value, first on ties).
    """
    ops = {'mode_first': lambda x: x.mode()[0]}
    spec = yaml.safe_load((Path(__file__).parent / 'specs.yaml').read_text())['agg_dict']
    return {col: ops.get(op, op) for col, op in spec.items()}


class Config(BaseSettings):
    """Configuration settings for the target microservice."""

    # Hopsworks settings
    project_name: str = Field(..., json_schema_extra={'env': 'PROJECT_NAME'})
    hopsworks_api_key: str = Field(..., json_schema_extra={'env': 'HOPSWORKS_API_KEY'})
    feature_group_version: int = Field(..., json_schema_extra={'env': 'FEATURE_GROUP_VERSION'})
    event_time: str = Field(..., json_schema_extra={'env': 'EVENT_TIME'})

    # Investment settings
    delta_period: int = Field(..., json_schema_extra={'env': 'DELTA_PERIOD'})
    filter_key: str = Field(..., json_schema_extra={'env': 'FILTER_KEY'})
    acquired_disposed: str = Field(..., json_schema_extra={'env': 'ACQUIRED_DISPOSED'})

    # Aggregation policy, loaded and resolved from specs.yaml at instantiation
    agg_dict: dict = Field(default_factory=_agg_dict)

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
