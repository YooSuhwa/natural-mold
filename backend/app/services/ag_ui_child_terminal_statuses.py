"""Read the admitted child outcomes already durable when a physical parent paused."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.ag_ui_visibility import ChildVisibility
from app.agent_runtime.lease_checkpoint_namespace import NS_SEP
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.models.conversation import Conversation
from app.models.conversation_child_task import ConversationChildTask
from app.models.conversation_run import ConversationRun
from app.services.child_task_read_bindings import readable_child_task_visibility


async def proven_child_terminal_statuses(
    db: AsyncSession,
    *,
    conversation: Conversation,
    context: OrgContext,
    run: ConversationRun,
    visibility: ChildVisibility,
) -> dict[tuple[str, ...], str]:
    """A resumed child or a change after the pause cannot alter this terminal replay."""
    if (
        run.status != "interrupted"
        or run.completed_at is None
        or run.conversation_id != conversation.id
        or not visibility.bindings
    ):
        return {}
    with db.no_autoflush:
        fresh = await readable_child_task_visibility(db, conversation, context, run.id)
        bindings = visibility.bindings & fresh.bindings
        if not bindings:
            return {}

        child = ConversationChildTask
        rows = await db.execute(
            select(child.id, child.tool_call_id, child.checkpoint_ns, child.status)
            .join(ConversationRun, ConversationRun.id == child.current_run_id)
            .join(Conversation, Conversation.id == ConversationRun.conversation_id)
            .where(
                child.id.in_([UUID(ident) for _, _, ident in bindings]),
                child.status.in_(("completed", "failed", "cancelled")),
                child.updated_at <= ConversationRun.completed_at,
                ConversationRun.id == run.id,
                ConversationRun.status == "interrupted",
                ConversationRun.completed_at == run.completed_at,
                ConversationRun.run_source == "CHAT",
                Conversation.id == conversation.id,
                private_scope_predicate(Conversation, context),
                private_scope_predicate(ConversationRun, context),
            )
        )
        statuses = {}
        for ident, call, path, status in rows:
            ns = tuple(path.split(NS_SEP))
            if (call, ns, str(ident)) in bindings:
                statuses[ns] = status
        return statuses
