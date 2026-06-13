"""Application settings loaded from environment variables via Pydantic."""

from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv
from paths import DATA_SOURCE_PATH, LOG_PATH, MODELS_DIR, PLOT_PATH
from pydantic import Field
from pydantic_settings import BaseSettings

# Load the main .env file and then the credentials.env file
load_dotenv('.env')
load_dotenv('.credentials.env')


@lru_cache
def _specs() -> dict:
    """Load the static feature-selection spec (column lists) from specs.yaml."""
    return yaml.safe_load((Path(__file__).parent / 'specs.yaml').read_text())


class Config(BaseSettings):
    """Typed application settings sourced from environment variables."""

    # Hopsworks settings
    project_name: str = Field(..., json_schema_extra={'env': 'PROJECT_NAME'})
    feature_group_training: str = Field(..., json_schema_extra={'env': 'FEATURE_GROUP_TRAINING'})
    feature_group_version: int = Field(..., json_schema_extra={'env': 'FEATURE_GROUP_VERSION'})
    api_key: str = Field(..., json_schema_extra={'env': 'API_KEY'})

    # Paths (defaults anchored on the repo layout via paths.py; env overridable)
    models_directory: str = Field(default=MODELS_DIR.str, json_schema_extra={'env': 'MODELS_DIRECTORY'})
    data_source_path: str = Field(default=DATA_SOURCE_PATH.str, json_schema_extra={'env': 'DATA_SOURCE_PATH'})
    plot_path: str = Field(default=PLOT_PATH.str, json_schema_extra={'env': 'PLOT_PATH'})
    log_path: str = Field(default=LOG_PATH.str, json_schema_extra={'env': 'LOG_PATH'})

    # Training settings
    max_runtime_secs: int = Field(..., json_schema_extra={'env': 'MAX_RUNTIME_SECS'})
    train_test_ratio: float = Field(..., json_schema_extra={'env': 'TRAIN_TEST_RATIO'})
    random_state: int = Field(..., json_schema_extra={'env': 'RANDOM_STATE'})

    # Feature Selection (column lists loaded from specs.yaml)
    target_feature: str = Field(..., json_schema_extra={'env': 'TARGET_FEATURE'})
    columns_to_drop: list = Field(default_factory=lambda: _specs()['columns_to_drop'])
    identification_features: list = Field(default_factory=lambda: _specs()['identification_features'])

    class Config:
        """Pydantic configuration for environment file loading."""

        # Specify only the main .env file here for Pydantic's internal use
        env_file = '.env'
        env_file_encoding = 'utf-8'


config = Config()
