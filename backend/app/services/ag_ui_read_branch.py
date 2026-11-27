"""Recognize the worker's branch activation when a reader follows a completed fork."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.chat_steer_checkpoint import run_checkpoint_for_steers
from app.agent_runtime.conversation_checkpointer import loaded_conversation_checkpointer
from app.authz.conversation_scope import require_private_conversation
from app.authz.org_context import OrgContext
from app.exceptions import AppError
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.services.ag_ui_read_access import require_read_run


async def accepts_fork_handoff(
    db: AsyncSession, conversation: Conversation, context: OrgContext, run: ConversationRun
) -> bool:
    """Only the fork's latest authorized root checkpoint may replace the previous selection."""
    selected = conversation.active_branch_checkpoint_id
    if not selected or run.conversation_id != conversation.id:
        return False
    try:
        with db.no_autoflush:
            owned = await require_read_run(
                db, conversation=conversation, context=context, run_id=run.id
            )
            if owned.source not in {"edit", "regenerate"} or owned.status != "completed":
                return False
            saver = await loaded_conversation_checkpointer(conversation, context, db=db)
            saved = await run_checkpoint_for_steers(
                conversation.id, owned.id, saver=saver, tenant_id=context.tenant_id
            )
            if not saved or saved.checkpoint.get("id") != selected:
                return False
            # The saver uses a different pool. Confirm the selected branch and actor
            # still match after that read, before continuing the public stream.
            current = await require_private_conversation(db, conversation.id, context)
            return current.active_branch_checkpoint_id == selected
    except (AppError, RuntimeError, AttributeError, TypeError):
        # An unavailable saver or an old unattributed checkpoint cannot establish continuity.
        return False
