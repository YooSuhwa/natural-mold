"""Personal account types never lent to others (plan 10.4, D15, Q4; E-05).

A credential a tool, MCP server, agent or agent skill binding carries is its owner's
(the ``shared`` mode of D15) unless the tool or MCP server is ``per_user`` (F-04): shared
directly, each caller connects their own account and it lends nothing (10.4: "per_user로
바꿔야 공유할 수 있습니다"); linked by a fixed-identity agent it still lends the owner's
connection, judged by its ``credential_definition_key`` (``per_user_carriers``). So:

* a personal credential is not shared (``credential#consumer`` would let the grantee
  attach someone's mail or booking account to their own tools) and not transferred
  (``share_transfer_blocks``);
* a tool, MCP server or agent that carries one (agents: through themselves or any
  subagent, ``personal_account_carriers``) is not shared;
* a tool does not take one on while it, or an agent using it (directly or as a
  subagent's tool), is shared (``PATCH /api/tools/{id}``); neither does a tool or MCP
  server switched back to ``shared`` (``PUT …/credential-mode``, F-04).

Refusals are ``409 personal_account_shared`` with ``details.personal_accounts``: one
``{resource_type, resource_id, credential_id, definition_key}`` per carrying resource.
Grants made before E-05 are not revoked; ``python -m app.authz.cli
personal-account-shares`` lists them for operators.

Attaching a carrier to an agent that is already shared (directly or as a subagent of
a shared agent) is refused the same way by ``attach_rules`` (E-06). Not guarded yet
(documented gaps): MCP server credential changes (``PATCH``) after the server is shared
(``mcp_service`` is over the size budget; the F-02 delegation validator re-checks), and
the MCP "조직 공용 계정" marking that would make ``mcp_oauth2`` non-personal.
"""

import uuid
from collections.abc import Iterable

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.error_codes import authorization_error
from app.authz.org_context import OrgContext
from app.authz.personal_account_carriers import Carrier, carriers, with_parents
from app.credentials.personal import is_personal_definition
from app.models.authz import ResourceGrant
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.tool import AgentToolLink, Tool

SHARE_KINDS = ("share", "migration")
SHARE_PRINCIPALS = ("user", "group", "organization")


async def personal_of(db: AsyncSession, found: Iterable[Carrier]) -> list[dict[str, str]]:
    found = list(found)
    ids = {cid for _, _, cid in found}
    if not ids:
        return []
    rows = await db.execute(
        select(Credential.id, Credential.definition_key).where(Credential.id.in_(ids))
    )
    keys = dict(rows.tuples().all())
    placeholders = await _placeholder_keys(db, [c for c in found if c[2] not in keys])
    keys.update(placeholders)
    return [
        {
            "resource_type": kind,
            "resource_id": str(rid),
            "credential_id": "" if cid in placeholders else str(cid),
            "definition_key": keys[cid],
        }
        for kind, rid, cid in found
        if cid in keys and is_personal_definition(keys[cid])
    ]


async def _placeholder_keys(db: AsyncSession, found: list[Carrier]) -> dict[uuid.UUID, str]:
    """``per_user`` carriers without a credential yet: the type the owner must connect."""
    keys: dict[uuid.UUID, str] = {}
    for kind, model in (("tool", Tool), ("mcp_server", McpServer)):
        ids = {rid for k, rid, cid in found if k == kind and cid == rid}
        if not ids:
            continue
        rows = await db.execute(
            select(model.id, model.credential_definition_key).where(
                model.id.in_(ids), model.credential_definition_key.is_not(None)
            )
        )
        keys.update({rid: key for rid, key in rows.tuples().all() if key is not None})
    return keys


async def personal_accounts(db: AsyncSession, row: object) -> list[dict[str, str]]:
    """Personal account credentials ``row`` would lend to whoever it is shared with."""
    return await personal_of(db, await carriers(db, row))


def _refuse(found: list[dict[str, str]]) -> None:
    if found:
        raise authorization_error("personal_account_shared", {"personal_accounts": found})


async def require_no_personal_accounts(db: AsyncSession, row: object) -> None:
    """Share time: the resource must not carry a personal account (10.4)."""
    _refuse(await personal_accounts(db, row))


async def has_share_grants(
    db: AsyncSession, org_id: uuid.UUID, kind: str, ids: set[uuid.UUID]
) -> bool:
    found = await db.scalar(
        select(ResourceGrant.id)
        .where(
            ResourceGrant.org_id == org_id,
            ResourceGrant.resource_type == kind,
            ResourceGrant.resource_id.in_(ids),
            ResourceGrant.state == "active",
            ResourceGrant.grant_kind.in_(SHARE_KINDS),
            ResourceGrant.principal_type.in_(SHARE_PRINCIPALS),
        )
        .limit(1)
    )
    return found is not None


def _agents_using(row: Tool | McpServer) -> Select[tuple[uuid.UUID]]:
    if isinstance(row, Tool):
        return select(AgentToolLink.agent_id).where(AgentToolLink.tool_id == row.id)
    return (
        select(AgentMcpToolLink.agent_id)
        .join(McpTool, McpTool.id == AgentMcpToolLink.mcp_tool_id)
        .where(McpTool.server_id == row.id)
    )


async def require_unshared_for_personal(
    db: AsyncSession, ctx: OrgContext, row: Tool | McpServer
) -> None:
    """Attach time: a tool or MCP server used by others cannot lend a personal account."""
    found = await personal_accounts(db, row)
    if not found:
        return
    kind = "tool" if isinstance(row, Tool) else "mcp_server"
    if await has_share_grants(db, ctx.org_id, kind, {row.id}):
        _refuse(found)
    users = set(await db.scalars(_agents_using(row)))
    if users and await has_share_grants(db, ctx.org_id, "agent", await with_parents(db, users)):
        _refuse(found)
