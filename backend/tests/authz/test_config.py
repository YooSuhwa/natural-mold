"""Rollout defaults and invalid configuration cannot silently enable authorization."""

import pytest
from pydantic import SecretStr, ValidationError
from pydantic_settings import SettingsConfigDict

from app.config import Settings


class IsolatedSettings(Settings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")


def test_rollout_preserves_legacy_behavior_when_unconfigured():
    # Given: no installation-specific settings.
    config = IsolatedSettings()
    # When: the authorization settings are resolved.
    # Then: production rollout remains opt-in.
    assert config.authz_mode == "legacy"
    assert config.deployment_mode == "single_tenant"
    assert config.db_rls_enabled is False
    assert config.authz_enforce_types == ""
    assert config.openfga_timeout_ms == 800


@pytest.mark.parametrize(
    "overrides",
    [
        {"authz_mode": "allow_all"},
        {"deployment_mode": "unknown"},
        {"openfga_timeout_ms": 0},
        {"authz_outbox_poll_seconds": 0},
        {"authz_shadow_max_rps": -1},
        {"registration_policy": "public"},
    ],
)
def test_configuration_rejects_invalid_rollout_values_when_supplied(overrides):
    # Given: an invalid installation override.
    # When / Then: boundary parsing rejects it.
    with pytest.raises(ValidationError):
        IsolatedSettings(**overrides)


def test_fga_token_is_redacted_when_settings_are_rendered():
    # Given: a token configured by an operator.
    config = IsolatedSettings(openfga_api_token=SecretStr("dummy-fga-token"))
    # When: diagnostics render settings.
    rendered = repr(config)
    # Then: the secret never appears.
    assert "dummy-fga-token" not in rendered
    assert config.openfga_api_token.get_secret_value() == "dummy-fga-token"
