"""Plain row snapshots consumed by ``tuples.py`` (plan 7.2); loaders map ORM rows here."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import JsonValue


@dataclass(frozen=True, slots=True)
class TenantMemberRow:
    tenant_id: UUID
    user_id: UUID
    status: str = "active"


@dataclass(frozen=True, slots=True)
class TenantRoleRow:
    tenant_id: UUID
    user_id: UUID
    role: str


@dataclass(frozen=True, slots=True)
class OrganizationRow:
    id: UUID
    tenant_id: UUID
    status: str = "active"


@dataclass(frozen=True, slots=True)
class OrgMemberRow:
    org_id: UUID
    user_id: UUID
    status: str = "active"


@dataclass(frozen=True, slots=True)
class OrgRoleRow:
    org_id: UUID
    role: str
    principal_type: str
    principal_id: UUID


@dataclass(frozen=True, slots=True)
class GroupRow:
    id: UUID
    org_id: UUID


@dataclass(frozen=True, slots=True)
class GroupMemberRow:
    group_id: UUID
    org_id: UUID
    user_id: UUID


@dataclass(frozen=True, slots=True)
class CapabilityRow:
    org_id: UUID
    capability: str
    principal_type: str
    principal_id: UUID


@dataclass(frozen=True, slots=True)
class ResourceRow:
    """agents, skills, tools, mcp_servers; owner is the table's ``user_id``."""

    resource_type: str
    id: UUID
    org_id: UUID | None
    owner_id: UUID | None
    is_system: bool = False
    archived_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CredentialRow:
    id: UUID
    org_id: UUID | None
    tenant_id: UUID | None
    owner_id: UUID | None
    scope: str = "personal"
    is_system: bool = False


@dataclass(frozen=True, slots=True)
class MarketplaceItemRow:
    """Owner is ``owner_user_id``; ``tenant_publication='approved'`` is company-public."""

    id: UUID
    org_id: UUID | None
    tenant_id: UUID | None
    owner_id: UUID | None
    is_system: bool = False
    tenant_publication: str = "none"


@dataclass(frozen=True, slots=True)
class McpToolRow:
    id: UUID
    server_id: UUID


@dataclass(frozen=True, slots=True)
class ModelRow:
    id: UUID
    is_visible: bool = True


@dataclass(frozen=True, slots=True)
class GrantRow:
    """``resource_grants`` row (C-01); fanout, allowed_org and delegation rows included.

    ``org_id`` is the organization the grant is scoped to: the resource's org for shares,
    the target org for system/tenant fanout and ``allowed_org`` rows, and the delegating
    *agent's* org for delegations (also when the delegated resource is a system one).
    ``tenant_id`` is ``resource_grants.tenant_id`` (the tenant of ``org_id``).
    """

    id: UUID
    org_id: UUID
    resource_type: str
    resource_id: UUID
    relation: str
    principal_type: str
    principal_id: UUID
    granted_at: datetime
    state: str = "active"
    grant_kind: str = "share"
    condition_name: str | None = None
    condition_context: dict[str, JsonValue] | None = None
    expires_at: datetime | None = None
    tenant_id: UUID | None = None


Row = (
    TenantMemberRow
    | TenantRoleRow
    | OrganizationRow
    | OrgMemberRow
    | OrgRoleRow
    | GroupRow
    | GroupMemberRow
    | CapabilityRow
    | ResourceRow
    | CredentialRow
    | MarketplaceItemRow
    | McpToolRow
    | GrantRow
)


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Every source row; ``expected_tuples`` applies the cross-row 7.2 filters."""

    tenant_members: tuple[TenantMemberRow, ...] = ()
    tenant_roles: tuple[TenantRoleRow, ...] = ()
    organizations: tuple[OrganizationRow, ...] = ()
    org_members: tuple[OrgMemberRow, ...] = ()
    org_roles: tuple[OrgRoleRow, ...] = ()
    groups: tuple[GroupRow, ...] = ()
    group_members: tuple[GroupMemberRow, ...] = ()
    capabilities: tuple[CapabilityRow, ...] = ()
    resources: tuple[ResourceRow, ...] = ()
    credentials: tuple[CredentialRow, ...] = ()
    marketplace_items: tuple[MarketplaceItemRow, ...] = ()
    mcp_tools: tuple[McpToolRow, ...] = ()
    models: tuple[ModelRow, ...] = ()
    grants: tuple[GrantRow, ...] = ()
