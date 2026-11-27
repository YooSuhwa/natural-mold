"""Verify outstanding approvals against their active, authorized checkpoint."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.protocol_events import ProtocolInterrupt
from app.authz.org_context import OrgContext
from app.models.conversation import Conversation
from app.services import conversation_run_service
from app.services.ag_ui_pending_checkpoint import read_pending_checkpoint
from app.services.ag_ui_read_target import ReadTarget


async def current_read_interrupts(
    db: AsyncSession, conversation: Conversation, context: OrgContext, target: ReadTarget
) -> Sequence[ProtocolInterrupt]:
    if target.run is None or target.run.status != "interrupted":
        return ()
    readable = await conversation_run_service.readable_conversation_run_ids(
        db, conversation, context
    )
    current = await conversation_run_service.get_latest_interrupted_run(
        db, conversation_id=conversation.id, context=context, readable_run_ids=readable
    )
    if current is None or current.id != target.run.id:
        return ()
    checkpoint = await read_pending_checkpoint(
        db, conversation=conversation, context=context, parent_run=target.run
    )
    return cast(Sequence[ProtocolInterrupt], checkpoint.interrupts) if checkpoint else ()
