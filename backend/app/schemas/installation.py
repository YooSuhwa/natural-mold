"""Authenticated installation capabilities for mode-sensitive navigation."""

from pydantic import BaseModel, ConfigDict

from app.tenancy.deployment import DeploymentMode


class InstallationResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    deployment_mode: DeploymentMode
    org_multi_enabled: bool
    platform_tenant_management_enabled: bool
