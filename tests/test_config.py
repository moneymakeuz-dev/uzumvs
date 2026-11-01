import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.config import Settings


def test_development_does_not_make_ai_calls_by_default():
    settings = Settings(_env_file=None)
    assert settings.ai_provider == "disabled"
    assert not settings.secure_cookies


@pytest.mark.parametrize("values", [
    {"database_url": "sqlite:///db.sqlite"},
    {"app_env": "production"},
    {"ai_provider": "gemini"},
    {"app_base_url": "javascript:alert(1)"},
    {"uzum_credentials_key": "not-a-fernet-key"},
])
def test_unsafe_configuration_is_rejected(values):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_secrets_are_not_exposed_in_settings_repr():
    key = Fernet.generate_key().decode()
    settings = Settings(_env_file=None, gemini_api_key="local-test-value", uzum_credentials_key=key)
    representation = repr(settings)
    assert "local-test-value" not in representation
    assert key not in representation
