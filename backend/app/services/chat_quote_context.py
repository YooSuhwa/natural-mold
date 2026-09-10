from __future__ import annotations

import hashlib
import json

from app.agent_runtime.message_utils import parse_msg_id
from app.models.conversation import Conversation
from app.schemas.chat_resource_context import (
    MAX_RESOURCE_CONTEXT_ITEM_BYTES,
    ChatResourceReference,
    FrozenChatResource,
)
from app.services.chat_resource_context_errors import (
    ResourceContextLimitError,
    ResourceContextNotFoundError,
)


async def freeze_message_quote(
    conversation: Conversation,
    reference: ChatResourceReference,
) -> FrozenChatResource:
    """Bind a user-selected excerpt to an owned, visible message at acceptance.

    Rendered Markdown selections need not be byte-identical to source Markdown.
    The selection is explicitly user-supplied reference data, never a verified
    verbatim quotation or trusted instruction. Message identity is server checked.
    """
    from app.agent_runtime.checkpointer import get_checkpointer
    from app.services.thread_branch_service import build_message_tree

    tree = await build_message_tree(
        get_checkpointer(),
        str(conversation.id),
        active_checkpoint_id=conversation.active_branch_checkpoint_id,
    )
    for index, node in enumerate(tree.nodes):
        message = node.message
        public_id = str(parse_msg_id(message.id, conversation.id, index))
        if reference.message_id not in {message.id, public_id}:
            continue
        if message.type not in {"human", "ai"}:
            raise ResourceContextNotFoundError()
        text = json.dumps(
            {
                "source_message_id": reference.message_id,
                "source_role": message.type,
                "user_selected_excerpt": reference.quote,
                "user_comment": reference.comment,
            },
            ensure_ascii=False,
        )
        byte_count = len(text.encode())
        if byte_count > MAX_RESOURCE_CONTEXT_ITEM_BYTES:
            raise ResourceContextLimitError(byte_count, MAX_RESOURCE_CONTEXT_ITEM_BYTES)
        return FrozenChatResource(
            kind="conversation",
            id=conversation.id,
            resolved_label=conversation.title or "Untitled conversation",
            label=conversation.title,
            message_id=reference.message_id,
            quote=reference.quote,
            comment=reference.comment,
            message_role="user" if message.type == "human" else "assistant",
            mime_type="text/plain",
            text=text,
            source_version=f"checkpoint:{tree.active_checkpoint_id}",
            content_sha256=hashlib.sha256(text.encode()).hexdigest(),
        )
    raise ResourceContextNotFoundError()
