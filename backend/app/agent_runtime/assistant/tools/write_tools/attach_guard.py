"""D17 for Assistant link writes (plan D17, 10.4, 11.7; E-06).

``commit_links`` replaces the plain ``session.commit()`` of a tool that appended links:
it checks the added account resources (``require_attachable``), flushes, re-runs the
shared-agent check (``require_shared_attach``) and commits — or rolls back and returns
the refusal as the tool's message. The Assistant never confirms on the user's behalf
(``confirm=False``): attaching an account to a shared agent is confirmed in the agent
settings, where the list is shown. The baseline is read before the pending links are
flushed, so callers must not flush between appending and calling this.

F-02: both helpers reconcile the agent's delegation grants with its links before the
commit (``sync_agent_delegations``) and write the queued tuples right after it;
``commit_relinked`` is the plain commit of a tool that removed links or added skills.
"""

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_delegations import (
    Key,
    claim_new_links,
    commit_and_apply,
    lock_delegations,
    sync_agent_delegations,
)
from app.authz.attach_rules import attach_baseline, require_attachable, require_shared_attach
from app.authz.org_context import OrgContext
from app.exceptions import AppError

_REFUSED = {
    "confirm_required": "공유된 에이전트에 계정 자원을 연결하려면 에이전트 설정 화면에서 "
    "저장하면서 위임 계정을 확인해야 합니다: {names}",
    "personal_account_shared": "공유된 에이전트에는 개인 계정 자격 증명을 쓰는 항목을 "
    "연결할 수 없습니다.",
    "permission_required": "이 계정 자원을 연결할 권한이 없습니다(공유 권한 필요).",
    "not_found": "이 계정 자원을 연결할 권한이 없습니다(공유 권한 필요).",
}


def _names(error: AppError) -> str:
    delegations = (error.details or {}).get("delegations")
    items = delegations if isinstance(delegations, list) else []
    return ", ".join(str(item.get("name")) for item in items if isinstance(item, dict))


async def commit_links(
    session: AsyncSession,
    ctx: OrgContext,
    agent_id: UUID,
    *,
    tool_ids: Iterable[UUID] = (),
    mcp_tool_ids: Iterable[UUID] = (),
) -> str | None:
    """Commit appended links, or roll back and return the D17 refusal message."""
    try:
        await lock_delegations(session, ctx)  # E-02 order: org row before the link writes
        added = await claim_new_links(session, ctx.user_id)
        with session.no_autoflush:
            before = await attach_baseline(session, agent_id)
        await require_attachable(session, ctx, tool_ids=tool_ids, mcp_tool_ids=mcp_tool_ids)
        await session.flush()
        await require_shared_attach(session, ctx, agent_id, before, confirm=False)
    except AppError as error:
        message = _REFUSED.get(error.code)
        if message is None:
            raise  # e.g. authz_unavailable: fail closed as every other tool call
        await session.rollback()
        return message.format(names=_names(error))
    await commit_relinked(session, ctx, agent_id, added=added)
    return None


async def commit_relinked(
    session: AsyncSession, ctx: OrgContext, agent_id: UUID, *, added: Iterable[Key] = ()
) -> None:
    """Grant the new links' objects (``added`` or the pending link rows), revoke unlinked
    delegations, commit and apply the tuples (F-02)."""
    await lock_delegations(session, ctx)
    added = {*added, *await claim_new_links(session, ctx.user_id)}
    await sync_agent_delegations(session, ctx, agent_id, added=added)
    await commit_and_apply(session)
