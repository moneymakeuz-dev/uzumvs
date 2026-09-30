import pytest
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
])
def test_unsafe_configuration_is_rejected(values):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_secrets_are_not_exposed_in_settings_repr():
    settings = Settings(_env_file=None, gemini_api_key="local-test-value")
    assert "local-test-value" not in repr(settings)