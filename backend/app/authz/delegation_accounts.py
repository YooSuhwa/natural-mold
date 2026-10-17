"""Account resources an agent lends to whoever runs it (D17 list, 10.6; E-06).

The share dialog shows this list before an agent is shared (``GET …/access``
``delegations``) and ``confirm_required`` carries it in ``details.delegations``. Items
come from the agent's current links (``personal_account_carriers``: its model credential,
tools, MCP servers and skill bindings, and its subagents', transitively) and from active
delegation grants (10.6; written from F-02 on). One item per carrying resource and
credential: ``{resource_type, resource_id, name, credential_id, credential_name,
owner_name}`` — whose account (``owner_name``, ``None`` for a company credential) and
which (``credential_name``). Carriers whose credential row is gone are skipped.
"""

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.personal_account_carriers import Carrier, agent_carriers
from app.models.agent import Agent
from app.models.authz import ResourceGrant
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.skill import Skill
from app.models.tool import Tool
from app.models.user import User

_NAMED: dict[str, type[Agent] | type[Skill] | type[Tool] | type[McpServer] | type[Credential]] = {
    "agent": Agent,
    "skill": Skill,
    "tool": Tool,
    "mcp_server": McpServer,
    "credential": Credential,
}
_CARRYING: dict[str, type[Tool] | type[McpServer]] = {"tool": Tool, "mcp_server": McpServer}


async def delegation_carriers(db: AsyncSession, agent_id: UUID) -> list[Carrier]:
    """Active delegation grants on credentials and credential-carrying tools/servers."""
    rows = await db.execute(
        select(ResourceGrant.resource_type, ResourceGrant.resource_id)
        .where(
            ResourceGrant.principal_type == "agent",
            ResourceGrant.principal_id == agent_id,
            ResourceGrant.grant_kind == "delegation",
            ResourceGrant.state == "active",
            ResourceGrant.resource_type.in_(("credential", *_CARRYING)),
        )
        .order_by(ResourceGrant.resource_type, ResourceGrant.resource_id)
    )
    found: list[Carrier] = []
    for kind, rid in rows.tuples().all():
        if kind == "credential":
            found.append((kind, rid, rid))
            continue
        model = _CARRYING[kind]
        cid = await db.scalar(select(model.credential_id).where(model.id == rid))
        if cid is not None:  # a tool/server without its owner's account lends nothing
            found.append((kind, rid, cid))
    return found


async def _names(db: AsyncSession, kind: str, ids: set[UUID]) -> dict[UUID, str]:
    model = _NAMED[kind]
    rows = await db.execute(select(model.id, model.name).where(model.id.in_(ids)))
    return dict(rows.tuples().all())


async def describe(db: AsyncSession, found: Iterable[Carrier]) -> list[dict[str, object]]:
    """The D17 items for ``found`` carriers (deduplicated, stable order)."""
    carriers = sorted(set(found), key=lambda c: (c[0], str(c[1]), str(c[2])))
    if not carriers:
        return []
    by_kind: dict[str, set[UUID]] = {}
    for kind, rid, _ in carriers:
        by_kind.setdefault(kind, set()).add(rid)
    names = {kind: await _names(db, kind, ids) for kind, ids in by_kind.items()}
    rows = await db.execute(
        select(Credential.id, Credential.name, Credential.user_id).where(
            Credential.id.in_({cid for _, _, cid in carriers})
        )
    )
    credentials = {cid: (name, owner) for cid, name, owner in rows.tuples().all()}
    owner_ids = {owner for _, owner in credentials.values() if owner is not None}
    owners: dict[UUID, str] = {}
    if owner_ids:
        found_owners = await db.execute(select(User.id, User.name).where(User.id.in_(owner_ids)))
        owners = dict(found_owners.tuples().all())
    items: list[dict[str, object]] = []
    for kind, rid, cid in carriers:
        if cid not in credentials:
            continue  # the credential is gone: nothing is lent
        credential_name, owner = credentials[cid]
        items.append(
            {
                "resource_type": kind,
                "resource_id": str(rid),
                "name": names[kind].get(rid, str(rid)),
                "credential_id": str(cid),
                "credential_name": credential_name,
                "owner_name": owners.get(owner) if owner is not None else None,
            }
        )
    return items


async def delegated_accounts(db: AsyncSession, agent_id: UUID) -> list[dict[str, object]]:
    """Everything sharing ``agent_id`` lends: current links plus delegation grants."""
    links = await agent_carriers(db, agent_id)
    return await describe(db, [*links, *await delegation_carriers(db, agent_id)])
