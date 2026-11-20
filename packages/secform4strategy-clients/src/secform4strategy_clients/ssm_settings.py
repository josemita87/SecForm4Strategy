"""SSM Parameter Store as a pydantic-settings source, for runtimes that cannot put secrets in the environment.

ECS reads a task's SSM parameters into its environment; Lambda cannot. A Lambda function instead
names its parameters in ``SSM_PARAMETERS``, a JSON object from settings field to parameter name, and
settings that inherit ``SsmSettings`` read them once, when built. Without the variable nothing is read.
"""

import json
import os
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import boto3
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from secform4strategy_clients.aws import RETRIES, classified
from secform4strategy_clients.ports import PermanentError

if TYPE_CHECKING:
    from mypy_boto3_ssm import SSMClient
    from pydantic.fields import FieldInfo

#: Environment variable naming the parameters to read: ``{"<field>": "<parameter name>"}``.
PARAMETERS_VARIABLE = 'SSM_PARAMETERS'


def parameter_values(ssm: SSMClient, parameters: Mapping[str, str]) -> dict[str, str]:
    """Return each field's decrypted parameter value.

    Args:
        ssm: The SSM client.
        parameters: Parameter name per settings field; GetParameters takes at most 10.

    Returns:
        The value per settings field.

    Raises:
        PermanentError: A parameter does not exist.
    """
    response = classified(
        'Reading SSM parameters', ssm.get_parameters, Names=list(parameters.values()), WithDecryption=True
    )
    if missing := response.get('InvalidParameters'):
        raise PermanentError(f'SSM parameters not found: {missing}')
    values = {parameter['Name']: parameter['Value'] for parameter in response['Parameters']}
    return {field: values[name] for field, name in parameters.items()}


class SsmSettingsSource(PydanticBaseSettingsSource):
    """The SSM parameters ``SSM_PARAMETERS`` names, read in one call."""

    def get_field_value(self, _field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        """Read nothing per field: ``__call__`` reads every parameter at once.

        Args:
            _field: The field, unused.
            field_name: The field's name.

        Returns:
            No value, the field name, and that the value is not complex.
        """
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        """Return the values of the parameters ``SSM_PARAMETERS`` names, or nothing without it.

        Returns:
            The value per settings field.
        """
        if not (parameters := os.environ.get(PARAMETERS_VARIABLE)):
            return {}
        return parameter_values(boto3.client('ssm', config=RETRIES), json.loads(parameters))


class SsmSettings(BaseSettings):
    """Settings that also read the SSM parameters ``SSM_PARAMETERS`` names; environment variables take precedence."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Read SSM after the arguments, the environment and the dotenv file.

        Args:
            settings_cls: The settings class.
            init_settings: Values passed to the constructor.
            env_settings: Environment variables.
            dotenv_settings: The dotenv file.
            file_secret_settings: The secrets directory.

        Returns:
            The sources, highest precedence first.
        """
        return init_settings, env_settings, dotenv_settings, SsmSettingsSource(settings_cls), file_secret_settings
