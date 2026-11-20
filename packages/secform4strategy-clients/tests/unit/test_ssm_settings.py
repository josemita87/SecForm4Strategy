"""Settings read the SSM parameters SSM_PARAMETERS names, and nothing without it.

Under test:
    packages/secform4strategy-clients/src/secform4strategy_clients/ssm_settings.py
"""

import boto3
import pytest
from botocore.stub import Stubber
from pydantic import SecretStr
from secform4strategy_clients.ports import PermanentError
from secform4strategy_clients.ssm_settings import SsmSettings, parameter_values


class Settings(SsmSettings):
    """A setting that may come from SSM."""

    token: SecretStr | None = None


@pytest.fixture
def ssm(monkeypatch: pytest.MonkeyPatch) -> Stubber:
    """An SSM client stubber; credentials are fake, as no call leaves the process."""
    monkeypatch.setenv('AWS_ACCESS_KEY_ID', 'test')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'test')
    return Stubber(boto3.client('ssm', region_name='us-east-1'))


class TestParameterValues:
    """Each field gets its parameter's decrypted value."""

    def test_values_are_returned_per_field(self, ssm: Stubber) -> None:
        names = {'token': '/app/token', 'user': '/app/user'}
        ssm.add_response(
            'get_parameters',
            {'Parameters': [{'Name': '/app/user', 'Value': 'u'}, {'Name': '/app/token', 'Value': 't'}]},
            {'Names': ['/app/token', '/app/user'], 'WithDecryption': True},
        )
        with ssm:
            assert parameter_values(ssm.client, names) == {'token': 't', 'user': 'u'}

    def test_a_missing_parameter_raises_permanent_error(self, ssm: Stubber) -> None:
        ssm.add_response('get_parameters', {'Parameters': [], 'InvalidParameters': ['/app/token']})
        with ssm, pytest.raises(PermanentError, match='/app/token'):
            parameter_values(ssm.client, {'token': '/app/token'})


class TestSsmSettings:
    """Without SSM_PARAMETERS, settings come from the environment alone."""

    def test_without_the_variable_nothing_is_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv('SSM_PARAMETERS', raising=False)
        monkeypatch.delenv('TOKEN', raising=False)
        assert Settings().token is None

    def test_the_environment_needs_no_ssm_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv('SSM_PARAMETERS', raising=False)
        monkeypatch.setenv('TOKEN', 'from-env')
        assert Settings().token == SecretStr('from-env')
