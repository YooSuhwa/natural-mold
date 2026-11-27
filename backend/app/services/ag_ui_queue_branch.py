"""Choose the execution branch of an admitted AG-UI queue item at claim time."""

import logging
from uuid import UUID

from fastapi import HTTPException
from psycopg import InterfaceError, OperationalError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import Exists

from app.agent_runtime.conversation_checkpointer import conversation_checkpointer
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.models.ag_ui_command_receipt import AgUiCommandReceipt
from app.models.conversation import Conversation
from app.models.conversation_run import utc_now_naive
from app.models.conversation_run_input import ConversationRunInput
from app.services.ag_ui_fork_checkpoints import selected_branch_head

logger = logging.getLogger(__name__)


def bound_ag_ui_input(context: OrgContext, conversation_id: UUID, input_id: UUID) -> Exists:
    return (
        select(AgUiCommandReceipt.input_id)
        .where(
            AgUiCommandReceipt.input_id == input_id,
            AgUiCommandReceipt.conversation_id == conversation_id,
            private_scope_predicate(AgUiCommandReceipt, context),
        )
        .exists()
    )


async def pin_selected_queue_head(
    db: AsyncSession, conversation: Conversation, queued: ConversationRunInput, context: OrgContext
) -> bool:
    selected = conversation.active_branch_checkpoint_id
    if selected is None or queued.source != "chat" or queued.checkpoint_id is not None:
        return True
    if not await db.scalar(select(bound_ag_ui_input(context, conversation.id, queued.id))):
        return True
    saver = await conversation_checkpointer(str(conversation.id), context, db=db)
    try:
        # The prior FIFO turn may have advanced this branch since admission.
        queued.checkpoint_id = await selected_branch_head(
            saver, str(conversation.id), selected, selected, None, tenant_id=context.tenant_id
        )
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        # Preserve the pending input for a new branch selection and explicit queue resume.
        conversation.queue_paused = True
        conversation.queue_paused_at = utc_now_naive()
        await db.flush()
        return False
    except (OperationalError, InterfaceError, OSError) as exc:
        # The saver uses its own pool. Let recovery revisit this pending input
        # while other conversations can still claim work.
        logger.warning("Queue checkpoint unavailable for %s (%s)", queued.id, type(exc).__name__)
        return False
    return True
