"""The company setting ``tenants.settings.marketplace_tenant_publish`` (plan 6.2, Q17)."""

from typing import Final

from pydantic import TypeAdapter, ValidationError

from app.models.organization import Tenant
from app.schemas.tenant_publication import TenantPublishPolicy

SETTING: Final = "marketplace_tenant_publish"
DEFAULT_POLICY: Final[TenantPublishPolicy] = "approval"  # 10.2 / S05: 기본 승인
_ADAPTER: Final = TypeAdapter(TenantPublishPolicy)


def publish_policy(tenant: Tenant) -> TenantPublishPolicy:
    """The stored policy; absent or unreadable values fall back to ``approval``.

    ``approval`` is the conservative fallback: nothing becomes company-public without
    a company administrator's decision.
    """
    raw = (tenant.settings or {}).get(SETTING, DEFAULT_POLICY)
    try:
        return _ADAPTER.validate_python(raw)
    except ValidationError:
        return DEFAULT_POLICY


def with_publish_policy(tenant: Tenant, policy: TenantPublishPolicy) -> None:
    """Replace the JSON value (a new dict, so the ORM sees the change)."""
    tenant.settings = {**(tenant.settings or {}), SETTING: policy}
