"""Personal account types never lent to others (plan 10.4, D15, Q4; E-05).

Today every credential a tool, MCP server, agent or agent skill binding carries is its
owner's (the ``shared`` mode of D15); per-user modes come with F-04/F-05. So, until then:

* a personal credential is not shared (``credential#consumer`` would let the grantee
  attach someone's mail or booking account to their own tools) and not transferred
  (``share_transfer_blocks``);
* a tool, MCP server or agent that carries one (agents: through themselves or any
  subagent, ``personal_account_carriers``) is not shared;
* a tool does not take one on while it, or an agent using it (directly or as a
  subagent's tool), is shared (``PATCH /api/tools/{id}``).

Refusals are ``409 personal_account_shared`` with ``details.personal_accounts``: one
``{resource_type, resource_id, credential_id, definition_key}`` per carrying resource.
Grants made before E-05 are not revoked; ``python -m app.authz.cli
personal-account-shares`` lists them for operators.

Not guarded yet (documented gaps): MCP server credential changes and agent
tool/skill/subagent attach after the resource is shared (``mcp_service`` and the agent
link writers are over the size budget; the F-02 delegation validator re-checks
attachments), and the MCP "조직 공용 계정" marking that would make ``mcp_oauth2``
non-personal.
"""

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.error_codes import authorization_error
from app.authz.org_context import OrgContext
from app.authz.personal_account_carriers import Carrier, carriers, with_parents
from app.credentials.personal import is_personal_definition
from app.models.authz import ResourceGrant
from app.models.credential import Credential
from app.models.tool import AgentToolLink, Tool

SHARE_KINDS = ("share", "migration")
SHARE_PRINCIPALS = ("user", "group", "organization")


async def _personal(db: AsyncSession, found: Iterable[Carrier]) -> list[dict[str, str]]:
    found = list(found)
    ids = {cid for _, _, cid in found}
    if not ids:
        return []
    rows = await db.execute(
        select(Credential.id, Credential.definition_key).where(Credential.id.in_(ids))
    )
    keys = dict(rows.tuples().all())
    return [
        {
            "resource_type": kind,
            "resource_id": str(rid),
            "credential_id": str(cid),
            "definition_key": keys[cid],
        }
        for kind, rid, cid in found
        if cid in keys and is_personal_definition(keys[cid])
    ]


async def personal_accounts(db: AsyncSession, row: object) -> list[dict[str, str]]:
    """Personal account credentials ``row`` would lend to whoever it is shared with."""
    return await _personal(db, await carriers(db, row))


def _refuse(found: list[dict[str, str]]) -> None:
    if found:
        raise authorization_error("personal_account_shared", {"personal_accounts": found})


async def require_no_personal_accounts(db: AsyncSession, row: object) -> None:
    """Share time: the resource must not carry a personal account (10.4)."""
    _refuse(await personal_accounts(db, row))


async def _shared(db: AsyncSession, org_id: uuid.UUID, kind: str, ids: set[uuid.UUID]) -> bool:
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


async def require_unshared_for_personal(db: AsyncSession, ctx: OrgContext, tool: Tool) -> None:
    """Attach time: a tool used by others cannot take a personal account on."""
    found = await personal_accounts(db, tool)
    if not found:
        return
    if await _shared(db, ctx.org_id, "tool", {tool.id}):
        _refuse(found)
    users = set(
        await db.scalars(select(AgentToolLink.agent_id).where(AgentToolLink.tool_id == tool.id))
    )
    if users and await _shared(db, ctx.org_id, "agent", await with_parents(db, users)):
        _refuse(found)
