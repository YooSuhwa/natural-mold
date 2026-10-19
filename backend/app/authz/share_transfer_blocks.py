"""Why an ownership transfer is refused before anything is written (10.4, 10.7; E-01).

Moving an owner column alone would leave work running as the previous owner, so a
transfer with such dependents answers ``409 transfer_blocked`` and lists them; the owner
(or the member-exit flow, E-02) stops or moves them first:

* agent: active triggers (they run as their owner, 9.8), active Agent API deployments and
  unrevoked API keys attached to them (owner-bound, 10.7 item 4);
* agent / tool / MCP server: its own credential column (``llm_credential_id`` /
  ``credential_id``) pointing at a credential the previous owner owns — the new owner
  would run on someone else's account;
* credential: personal account types (10.4, the definition's ``personal`` flag, E-05)
  are never transferred — the account belongs to a person.

During a member exit (E-02) the dependents owned by the leaving member are not blockers:
the same transaction pauses their triggers, disables their deployments and revokes their
keys (``org_member_exit.stop_locked_member_entry_points``).

Not checked (stay with their rows, documented gaps): an agent's skill/tool/MCP links
to the previous owner's private resources, its conversations and memories (user data
of each participant), and the previous owner's per-user credential bindings.
"""

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.error_codes import authorization_error
from app.authz.share_targets import ShareRow
from app.credentials.personal import is_personal_definition
from app.models.agent import Agent
from app.models.agent_api import AgentApiKey, AgentApiKeyDeployment, AgentDeployment
from app.models.agent_trigger import AgentTrigger
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.tool import Tool


def _ids(rows: Iterable[UUID]) -> list[str]:
    return [str(value) for value in rows]


async def _agent_dependents(
    db: AsyncSession, agent_id: UUID, exiting: UUID | None
) -> dict[str, list[str]]:
    triggers = await db.scalars(
        select(AgentTrigger.id).where(
            AgentTrigger.agent_id == agent_id,
            AgentTrigger.status == "active",
            AgentTrigger.user_id != exiting if exiting else true(),
        )
    )
    deployments = list(
        await db.scalars(
            select(AgentDeployment.id).where(
                AgentDeployment.agent_id == agent_id,
                AgentDeployment.status == "active",
                AgentDeployment.user_id != exiting if exiting else true(),
            )
        )
    )
    keys = await db.scalars(
        select(AgentApiKey.id)
        .join(AgentApiKeyDeployment, AgentApiKeyDeployment.api_key_id == AgentApiKey.id)
        .where(
            AgentApiKeyDeployment.deployment_id.in_(deployments),
            AgentApiKey.revoked_at.is_(None),
            AgentApiKey.user_id != exiting if exiting else true(),
        )
        .distinct()
    )
    return {"triggers": _ids(triggers), "deployments": _ids(deployments), "api_keys": _ids(keys)}


async def _owner_credentials(db: AsyncSession, row: ShareRow, previous: UUID) -> list[str]:
    match row:
        case Agent():
            bound = row.llm_credential_id
        case Tool() | McpServer():
            bound = row.credential_id
        case _:
            return []
    if bound is None:
        return []
    owner = await db.scalar(select(Credential.user_id).where(Credential.id == bound))
    return [str(bound)] if owner == previous else []


async def check_transferable(
    db: AsyncSession, row: ShareRow, previous: UUID | None, *, exiting: UUID | None = None
) -> None:
    """Raise ``409 transfer_blocked`` with the blocking dependents; nothing is written.

    ``exiting``: the member whose exit stops their own entry points in this transaction.
    """
    if isinstance(row, Credential) and is_personal_definition(row.definition_key):
        raise authorization_error("transfer_blocked", {"personal_account": row.definition_key})
    found: dict[str, list[str]] = {}
    if isinstance(row, Agent):
        found.update(await _agent_dependents(db, row.id, exiting))
    if previous is not None:
        found["credentials"] = await _owner_credentials(db, row, previous)
    blocking = {name: ids for name, ids in found.items() if ids}
    if blocking:
        raise authorization_error("transfer_blocked", blocking)
