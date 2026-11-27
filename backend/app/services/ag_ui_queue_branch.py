"""Choose the execution branch of an admitted AG-UI queue item at claim time."""

import logging
from uuid import UUID

from fastapi import HTTPException
from psycopg import InterfaceError, OperationalError
from sqlalchemy import Select, Text, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import Exists

from app.agent_runtime.conversation_checkpointer import conversation_checkpointer
from app.authz.conversation_run_scope import private_run_predicate
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.models.ag_ui_command_receipt import AgUiCommandReceipt
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun, utc_now_naive
from app.models.conversation_run_input import ConversationRunInput
from app.services.ag_ui_fork_checkpoints import selected_branch_head

logger = logging.getLogger(__name__)
DEFAULT_BRANCH_AT_CLAIM = "ag_ui_default_branch_at_claim"


def input_projection_version(
    context: OrgContext, conversation_id: UUID, input_id: UUID
) -> Select[tuple[str]]:
    return select(AgUiCommandReceipt.projection_version).where(
        AgUiCommandReceipt.input_id == input_id,
        AgUiCommandReceipt.conversation_id == conversation_id,
        private_scope_predicate(AgUiCommandReceipt, context),
    )


def bound_ag_ui_input(context: OrgContext, conversation_id: UUID, input_id: UUID) -> Exists:
    return input_projection_version(context, conversation_id, input_id).exists()


async def claimed_default_branch(
    db: AsyncSession, context: OrgContext, queued: ConversationRunInput
) -> bool:
    # SQLite represents JSON true as integer 1 when extracted; json_type preserves
    # the difference. PostgreSQL's JSON text distinguishes true from "true".
    marker = ConversationRun.metadata_json[DEFAULT_BRANCH_AT_CLAIM]
    is_true = (
        func.json_type(ConversationRun.metadata_json, f"$.{DEFAULT_BRANCH_AT_CLAIM}") == "true"
        if db.get_bind().dialect.name == "sqlite"
        else cast(marker, Text) == "true"
    )
    return bool(
        await db.scalar(
            select(is_true).where(
                ConversationRun.id == queued.run_id,
                ConversationRun.conversation_id == queued.conversation_id,
                ConversationRun.agent_id == queued.agent_id,
                private_run_predicate(context),
            )
        )
    )


async def pin_selected_queue_head(
    db: AsyncSession, conversation: Conversation, queued: ConversationRunInput, context: OrgContext
) -> bool:
    selected = conversation.active_branch_checkpoint_id
    if selected is None or queued.source != "chat" or queued.checkpoint_id is not None:
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
