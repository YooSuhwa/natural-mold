"""Select the branch actually authored by a durable fork at its terminal boundary."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.conversation_checkpointer import loaded_conversation_checkpointer
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.models.ag_ui_command_receipt import AgUiCommandReceipt
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.tenancy.checkpoint_audit import CHECKPOINT_TENANT_KEY

FORK_SELECTION_KEY = "ag_ui_fork_selected_checkpoint"


def tracks_fork_branch(run: ConversationRun | None) -> bool:
    return bool(
        run is not None
        and run.source in {"edit", "regenerate"}
        and FORK_SELECTION_KEY in (run.metadata_json or {})
    )


async def adopt_fork_branch(
    db: AsyncSession, conversation: Conversation, context: OrgContext, run: ConversationRun
) -> None:
    """The private conversation and the finalizing run are locked in canonical order."""
    from app.services.ag_ui_pending_checkpoint import _selected_head  # noqa: PLC2701

    if not tracks_fork_branch(run) or run.conversation_id != conversation.id:
        return
    expected = (run.metadata_json or {}).get(FORK_SELECTION_KEY)
    if conversation.active_branch_checkpoint_id != expected:
        return
    fork_point = await db.scalar(
        select(AgUiCommandReceipt.checkpoint_id).where(
            AgUiCommandReceipt.run_id == run.id,
            AgUiCommandReceipt.conversation_id == conversation.id,
            private_scope_predicate(AgUiCommandReceipt, context),
        )
    )
    if not fork_point:
        return
    saver = await loaded_conversation_checkpointer(conversation, context, db=db)
    head = await _selected_head(saver, str(conversation.id), context.tenant_id, fork_point)
    if (
        head is None
        or head.metadata.get("moldy_run_id") != str(run.id)
        or head.metadata.get(CHECKPOINT_TENANT_KEY) != str(context.tenant_id)
        or head.checkpoint["id"] == fork_point
    ):
        return
    await db.execute(
        update(Conversation)
        .where(
            Conversation.id == conversation.id,
            private_scope_predicate(Conversation, context),
            Conversation.active_branch_checkpoint_id.is_not_distinct_from(expected),
        )
        .values(active_branch_checkpoint_id=head.checkpoint["id"])
    )
