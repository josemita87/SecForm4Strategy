"""Application configuration: environment variables, SSM parameters for the Alpaca keys, and the YAML spec."""

from typing import Literal

import yaml
from paths import SPEC_PATH
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic_settings import SettingsConfigDict
from secform4strategy_clients.ssm_settings import SsmSettings


class S3Models(BaseModel):
    """Versioned models in an S3 bucket, as training saves them."""

    backend: Literal['s3']
    region: str
    bucket: str
    prefix: str = 'models'


class Importance(BaseModel):
    """Permutation importance: repeats, seed, the most rows sampled, and the features kept."""

    model_config = ConfigDict(frozen=True, extra='forbid')

    n_repeats: int = Field(gt=0)
    random_state: int
    max_rows: int = Field(gt=0)
    top: int = Field(gt=0)


class ExploreWindow(BaseModel):
    """Recent insider sales: the window in days, the length of each list, and the rebuild interval in minutes."""

    model_config = ConfigDict(frozen=True, extra='forbid')

    days: int = Field(gt=0)
    limit: int = Field(gt=0)
    every_minutes: int = Field(gt=0, le=60)

    @field_validator('every_minutes')
    @classmethod
    def _divides_the_hour(cls, minutes: int) -> int:
        """Reject an interval that does not divide 60, which would rebuild explore.json unevenly.

        Args:
            minutes: The configured interval.

        Returns:
            The interval.

        Raises:
            ValueError: If 60 is not a multiple of it.
        """
        if 60 % minutes:
            raise ValueError(f'every_minutes must divide 60, got {minutes}')
        return minutes


class Spec(BaseModel):
    """The static part of ``spec.yaml``; ``feature_groups`` is validated by ``load_feature_group_catalog``."""

    model_config = ConfigDict(frozen=True, extra='ignore')

    live_functions: tuple[str, ...]
    live_runs: int = Field(gt=0, le=1000)
    importance: Importance
    explore: ExploreWindow
    orders_limit: int = Field(gt=0, le=500)
    equity_period: str
    equity_timeframe: str


class Config(SsmSettings):
    """Settings for the site bucket, the AWS resources reported on, Alpaca, the feature store and the model store."""

    # Region of the AWS resources reported on; Lambda sets AWS_REGION
    aws_region: str
    site_bucket: str
    name_prefix: str = 'secform4strategy'
    live_score_state_machine_arn: str
    retrain_state_machine_arn: str
    poller_schedule_name: str
    retrain_schedule_name: str
    # The threshold inference trades at, shown in live.json; model.json's for models saved without one
    prediction_threshold: float = Field(le=0)

    # Alpaca API credentials; the Lambda reads them from SSM (SSM_PARAMETERS)
    alpaca_api_key: SecretStr
    alpaca_api_secret: SecretStr
    alpaca_base_url: str

    # Iceberg feature store; the catalog itself is configured by PYICEBERG_CATALOG__DEFAULT__* (docs/configuration.md)
    feature_store_namespace: str = 'secform4strategy'
    feature_group_version: int

    # Model store: MODEL_STORE__<FIELD> sets its fields
    model_store: S3Models

    spec: Spec = Field(default_factory=lambda: Spec.model_validate(yaml.safe_load(SPEC_PATH.read_text())))

    # `model_store` is a setting, not a pydantic internal
    model_config = SettingsConfigDict(env_nested_delimiter='__', protected_namespaces=('settings_',))


config = Config()
