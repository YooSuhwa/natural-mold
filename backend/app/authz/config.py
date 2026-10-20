"""Typed installation settings for the staged authorization rollout."""

from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings


class AuthzSettings(BaseSettings):
    """Settings mixin; mutable to support existing test/installation overrides."""

    deployment_mode: Literal["single_tenant", "multi_tenant"] = "single_tenant"
    default_tenant_name: str = "기본 회사"
    tenant_default_limits: str = "{}"
    db_rls_enabled: bool = False
    migration_database_url: str = ""
    data_key_provider: Literal["local", "transit"] = "local"
    tenant_data_keys_enabled: bool = False
    sso_enabled: bool = True
    sso_public_base_url: str = ""  # H-08: public Moldy URL for IdP redirect URIs (else request)
    authz_mode: Literal["legacy", "shadow", "enforce"] = "legacy"
    authz_enforce_types: str = ""
    openfga_api_url: str = "http://openfga:8080"
    openfga_api_token: SecretStr = SecretStr("")
    openfga_ca_cert: str = ""
    vault_ca_cert: str = ""
    credential_http_allowed_hosts: str = ""  # 11.9: http allowed for credentialed calls
    outbound_private_allowed_hosts: str = ""  # 11.9: exempt from the SSRF private-IP block
    credential_empty_hosts_policy: Literal["warn", "enforce"] = "warn"  # D18/Q14 rollout
    internal_plaintext_hosts: str = ""  # 15.2.1: hosts exempt from the production TLS check
    openfga_store_name: str = "moldy"
    openfga_store_id: str = ""
    openfga_model_id: str = ""
    openfga_timeout_ms: int = Field(default=800, gt=0)
    authz_outbox_poll_seconds: float = Field(default=2, gt=0)
    authz_reconcile_cron: str = "0 3 * * *"
    authz_delegation_cron: str = "30 3 * * *"  # F-03 daily delegation validator (10.6)
    authz_shadow_max_rps: int = Field(default=50, ge=0)
    # personal_tenant: an uninvited signup gets its own company and organization.
    registration_policy: Literal["default_org", "personal_tenant", "invite_only", "closed"] = (
        "default_org"
    )
    default_org_name: str = "기본 조직"
    org_multi_enabled: bool = True
    authz_trusted_proxy_cidrs: str = ""

    @model_validator(mode="after")
    def _personal_tenants_need_multi_tenant(self) -> "AuthzSettings":
        # single_tenant shares file storage across companies and lets a company admin
        # manage its own usage limits; neither is safe for self-service companies.
        if self.registration_policy == "personal_tenant" and self.deployment_mode != (
            "multi_tenant"
        ):
            raise ValueError("REGISTRATION_POLICY=personal_tenant requires multi_tenant")
        return self
