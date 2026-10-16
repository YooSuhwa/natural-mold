"""Does a message id belong to a conversation? (D-10 feedback membership, plan 9.3)

Messages have no table: the active branch of the LangGraph checkpoint is the source,
walked the same way the read path numbers them (``parse_msg_id(raw, conversation,
idx)``), so both the raw LangGraph id and the public ``MessageResponse.id`` match.
A purged conversation's retained transcript is read through the regular reader.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.message_utils import parse_msg_id
from app.authz.org_context import OrgContext
from app.models.conversation import Conversation


async def conversation_has_message(
    db: AsyncSession, conversation: Conversation, message_id: str, context: OrgContext
) -> bool:
    if conversation.agent_lifecycle_state == "purged":
        from app.services.chat.messages import list_messages_from_checkpointer

        retained = await list_messages_from_checkpointer(db, conversation, context=context)
        return any(str(message.id) == message_id for message in retained)

    from app.agent_runtime.checkpointer import get_checkpointer
    from app.services.thread_branch_service import build_message_tree

    tree = await build_message_tree(
        get_checkpointer(),
        str(conversation.id),
        active_checkpoint_id=conversation.active_branch_checkpoint_id,
    )
    for idx, node in enumerate(tree.nodes):
        raw = getattr(node.message, "id", None)
        if message_id == raw or message_id == str(parse_msg_id(raw, conversation.id, idx)):
            return True
    return False
