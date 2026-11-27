"""Resolve the public spawning run of a separately executed child conversation."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_scope import require_private_conversation
from app.authz.independent_job_history import readable_job_conversations
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.exceptions import AppError
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.conversation_run import ConversationRun
from app.models.independent_chat_job import IndependentChatJob
from app.services.ag_ui_commands import load_public_run_id, load_run_binding
from app.services.ag_ui_read_access import require_read_run


async def spawned_parent_public_id(
    db: AsyncSession, conversation: Conversation, context: OrgContext, run: ConversationRun
) -> str | None:
    """Return a parent only for the initial run recorded in an authorized job handoff."""
    if (
        conversation.source != INDEPENDENT_JOB_SOURCE
        or run.source != "start"
        or run.parent_run_id is not None
        or run.conversation_id != conversation.id
    ):
        return None
    origin = (
        select(
            IndependentChatJob.id,
            IndependentChatJob.parent_conversation_id,
            IndependentChatJob.parent_run_id,
        )
        .join(ConversationRun, ConversationRun.id == IndependentChatJob.child_run_id)
        .where(
            private_scope_predicate(IndependentChatJob, context),
            private_scope_predicate(ConversationRun, context),
            IndependentChatJob.child_conversation_id == conversation.id,
            ConversationRun.id == run.id,
            ConversationRun.conversation_id == conversation.id,
            ConversationRun.agent_id == IndependentChatJob.child_agent_id,
            ConversationRun.source == "start",
            ConversationRun.run_source == "CHAT",
            ConversationRun.parent_run_id.is_(None),
        )
        .limit(2)
    )
    with db.no_autoflush:
        rows = (await db.execute(origin)).all()
        if len(rows) != 1:
            return None
        job_id, parent_thread, parent_run = rows[0]
        try:
            parent = await require_private_conversation(db, parent_thread, context)
            await require_read_run(db, conversation=parent, context=context, run_id=parent_run)
        except AppError as exc:
            if exc.status not in (403, 404):
                raise
            return None
        public_id = await load_public_run_id(db, context, parent_thread, parent_run)
        candidate = public_id or str(parent_run)
        binding = await load_run_binding(db, context, parent_thread, candidate)
        if binding is None:
            if public_id is not None:
                return None
        elif binding.run_id != parent_run:
            # An opaque public ID can look like an older native run's physical UUID.
            return None
        # This proof checks both agents' current grants and the parent run's exact
        # private thread. Re-read the relation after remote authorization as well.
        visible = await readable_job_conversations(db, context, job_ids=[job_id])
        if visible.get(job_id) != conversation.id or (await db.execute(origin)).all() != rows:
            return None
        return candidate
