"""The company tier of the catalog (plan 7.2, 10.3, S07; E-07).

Availability narrows platform → company → organization; no tier can widen the one above:

* platform: ``models.is_visible`` and enabled system tools / MCP servers (``available``);
* company: ``tenants.settings.catalog_policy`` off switches, the tenant limit
  ``allow_system_models`` (explicitly ``false`` removes every platform model; absent keeps
  them, which is how the migrated default company behaves) and per-model organization
  designation ``tenants.settings.model_orgs``: a platform model without a designation
  reaches every organization, a company model only the organizations designated for it;
* organization: ``organizations.settings.catalog_policy`` (E-03).

Company shared credentials (``credentials.scope = 'tenant'``) are designated the same way
(``tenants.settings.credential_orgs``) and fan out as ``allowed_org`` + ``consumer``.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import ColumnElement, or_

from app.models.model import Model
from app.models.organization import Tenant
from app.schemas.org_catalog import CatalogKind, CatalogPolicy

POLICY_KEY = "catalog_policy"
MODEL_ORGS_KEY = "model_orgs"
CREDENTIAL_ORGS_KEY = "credential_orgs"
type DesignationKey = Literal["model_orgs", "credential_orgs"]
type Designations = Mapping[UUID, frozenset[UUID]]


def stored_policy(settings: Mapping[str, object] | None) -> CatalogPolicy:
    return CatalogPolicy.model_validate((settings or {}).get(POLICY_KEY, {}))


def designations(settings: Mapping[str, object] | None, key: DesignationKey) -> Designations:
    raw = (settings or {}).get(key)
    if not isinstance(raw, Mapping):
        return {}
    found: dict[UUID, frozenset[UUID]] = {}
    for item, orgs in raw.items():
        if isinstance(orgs, list):
            found[UUID(str(item))] = frozenset(UUID(str(org)) for org in orgs)
    return found


def _ids(values: Iterable[UUID]) -> list[JsonValue]:
    return [str(value) for value in sorted(set(values))]


def with_designation(
    settings: Mapping[str, JsonValue] | None,
    key: DesignationKey,
    item_id: UUID,
    org_ids: Iterable[UUID] | None,
) -> dict[str, JsonValue]:
    """New settings with ``item_id`` designated to ``org_ids`` (``None`` drops the entry)."""
    current: dict[str, JsonValue] = {
        str(item): _ids(orgs) for item, orgs in designations(settings, key).items()
    }
    current.pop(str(item_id), None)
    if org_ids is not None:
        current[str(item_id)] = _ids(org_ids)
    return {**(settings or {}), key: current}


def model_scope(tenant_id: UUID | None) -> ColumnElement[bool]:
    """Platform models, plus ``tenant_id``'s company models when given."""
    platform = Model.tenant_id.is_(None)
    return platform if tenant_id is None else or_(platform, Model.tenant_id == tenant_id)


def allows_system_models(tenant: Tenant) -> bool:
    return (tenant.limits or {}).get("allow_system_models", True) is not False


@dataclass(frozen=True, slots=True)
class CompanyTier:
    policy: CatalogPolicy = field(default_factory=CatalogPolicy)
    allow_system_models: bool = True
    model_orgs: Designations = field(default_factory=dict)
    credential_orgs: Designations = field(default_factory=dict)

    def offers(self, kind: CatalogKind, item_id: UUID, source: str, *, available: bool) -> bool:
        """The company keeps the item at all (before any organization designation)."""
        if not available or not self.policy.enabled_for(kind, item_id):
            return False
        return not (kind == "model" and source == "platform" and not self.allow_system_models)

    def reaches(self, kind: CatalogKind, item_id: UUID, source: str, org_id: UUID) -> bool:
        """The company designation lets the item reach ``org_id`` (models only)."""
        if kind != "model":
            return True
        designated = self.model_orgs.get(item_id)
        if designated is None:
            return source == "platform"
        return org_id in designated

    def credential_reaches(self, credential_id: UUID, org_id: UUID) -> bool:
        return org_id in self.credential_orgs.get(credential_id, frozenset())


def company_tier(tenant: Tenant) -> CompanyTier:
    return CompanyTier(
        policy=stored_policy(tenant.settings),
        allow_system_models=allows_system_models(tenant),
        model_orgs=designations(tenant.settings, MODEL_ORGS_KEY),
        credential_orgs=designations(tenant.settings, CREDENTIAL_ORGS_KEY),
    )
