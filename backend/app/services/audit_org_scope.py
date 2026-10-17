"""Which organization an audit event belongs to (plan 14.1, 6.2 m89 ``audit_events.org_id``).

Precedence:

1. the caller's explicit ``org_id``;
2. an org-scoped target row (agent, skill, tool, MCP server, credential, conversation,
   trigger, group, invitation, marketplace item/installation, Agent API deployment/key,
   skill builder session): its own ``org_id`` — the identity map, else one select of
   ``(tenant_id, org_id)`` only (no private payload columns), no autoflush.
   Such events never take the session's organization: acting from org A on an org B
   resource is attributed to B, and a vanished or org-less (system) row stays empty
   unless ``metadata["org_id"]`` names an organization;
3. the organization the event is about (``target_type="organization"`` or
   ``metadata["org_id"]``), accepted only when it is in the same company;
4. for other targets only, the organization of the session's context.

An organization from another company is never paired (``(org_id, tenant_id)`` FK).
"""

from collections.abc import Iterator, Mapping
from typing import Any, Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.util import identity_key

from app.database import session_org, session_tenant
from app.models.agent import Agent
from app.models.agent_api import AgentApiKey, AgentDeployment
from app.models.agent_trigger import AgentTrigger
from app.models.conversation import Conversation
from app.models.credential import Credential
from app.models.marketplace import MarketplaceInstallation, MarketplaceItem
from app.models.mcp_server import McpServer
from app.models.organization import Organization
from app.models.organization_group import Group, OrgInvitation
from app.models.skill import Skill
from app.models.skill_builder_session import SkillBuilderSession
from app.models.tool import Tool

type Scoped = tuple[UUID | None, UUID | None]
ORG_SCOPED_TARGETS: Final[dict[str, Any]] = {
    "agent": Agent,
    "skill": Skill,
    "tool": Tool,
    "mcp_server": McpServer,
    "credential": Credential,
    "conversation": Conversation,
    "trigger": AgentTrigger,
    "group": Group,
    "org_invitation": OrgInvitation,
    "marketplace_item": MarketplaceItem,
    "marketplace_installation": MarketplaceInstallation,
    "agent_deployment": AgentDeployment,
    "agent_api_key": AgentApiKey,
    "skill_builder_session": SkillBuilderSession,
}


def _uuid(value: object) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            return None
    return None


async def _row_scope(db: object, model: Any, row_id: UUID) -> Scoped | None:
    """Scope columns only: a loaded row from the identity map, else a 2-column select."""
    if not isinstance(db, AsyncSession):  # unit-test doubles carry no rows
        return None
    org_attr = "id" if model is Organization else "org_id"  # an organization is its own org
    loaded = db.sync_session.identity_map.get(identity_key(model, row_id))
    if loaded is not None:
        return getattr(loaded, "tenant_id", None), getattr(loaded, org_attr, None)
    with db.no_autoflush:  # never flush the caller; never load private payload columns
        query = select(model.tenant_id, getattr(model, org_attr)).where(model.id == row_id)
        row = (await db.execute(query)).first()
    return None if row is None else (row[0], row[1])


def _named_orgs(
    target_type: str, target_id: object, metadata: Mapping[str, object] | None
) -> Iterator[UUID]:
    if target_type == "organization" and (target := _uuid(target_id)) is not None:
        yield target
    if metadata is not None and (named := _uuid(metadata.get("org_id"))) is not None:
        yield named


async def _named_org(db: object, tenant_id: UUID | None, candidates: Iterator[UUID]) -> Scoped:
    for candidate in candidates:
        scope = await _row_scope(db, Organization, candidate)
        if scope is not None and tenant_id in (None, scope[0]):
            return scope
    return tenant_id, None


async def event_org_scope(
    db: object,
    *,
    tenant_id: UUID | None,
    org_id: UUID | None,
    target_type: str,
    target_id: object,
    metadata: Mapping[str, object] | None,
) -> Scoped:
    """Return ``(tenant_id, org_id)`` for the row; ``tenant_id`` is filled only when empty."""
    if org_id is not None:
        return tenant_id, org_id
    model = ORG_SCOPED_TARGETS.get(target_type)
    if model is not None:
        row_id = _uuid(target_id)
        scope = await _row_scope(db, model, row_id) if row_id is not None else None
        if scope is not None and scope[1] is not None and tenant_id in (None, scope[0]):
            return scope[0], scope[1]
        return await _named_org(db, tenant_id, _named_orgs("", None, metadata))
    resolved = await _named_org(db, tenant_id, _named_orgs(target_type, target_id, metadata))
    if resolved[1] is not None:
        return resolved
    bound = session_org(db)
    if bound is not None and tenant_id is not None and tenant_id == session_tenant(db):
        return tenant_id, bound
    return tenant_id, None
