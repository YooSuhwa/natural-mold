"""Typed installation settings for the staged authorization rollout."""

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings


class AuthzSettings(BaseSettings):
    """Settings mixin; mutable to support existing test/installation overrides."""

    deployment_mode: Literal["single_tenant", "multi_tenant"] = "single_tenant"
    default_tenant_name: str = "기본 회사"
    tenant_default_limits: str = "{}"
    db_rls_enabled: bool = False
    migration_database_url: str = ""
    data_key_provider: Literal["local", "transit"] = "local"
    sso_enabled: bool = True
    authz_mode: Literal["legacy", "shadow", "enforce"] = "legacy"
    authz_enforce_types: str = ""
    openfga_api_url: str = "http://openfga:8080"
    openfga_api_token: SecretStr = SecretStr("")
    openfga_ca_cert: str = ""
    vault_ca_cert: str = ""
    credential_http_allowed_hosts: str = ""
    internal_plaintext_hosts: str = ""
    openfga_store_name: str = "moldy"
    openfga_store_id: str = ""
    openfga_model_id: str = ""
    openfga_timeout_ms: int = Field(default=800, gt=0)
    authz_outbox_poll_seconds: float = Field(default=2, gt=0)
    authz_reconcile_cron: str = "0 3 * * *"
    authz_shadow_max_rps: int = Field(default=50, ge=0)
    registration_policy: Literal["default_org", "invite_only", "closed"] = "default_org"
    default_org_name: str = "기본 조직"
    org_multi_enabled: bool = True
    authz_trusted_proxy_cidrs: str = ""
