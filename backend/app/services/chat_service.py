"""Chat service — conversations, messages, and agent context assembly.

Greenfield M5 rewrite. The legacy PREBUILT/CUSTOM/MCP branching has been
collapsed into a single resolution path: every tool row points at a registered
``ToolDefinition`` (``tool.definition_key``) plus an optional credential
(``tool.credential_id``). MCP server bindings are handled separately by the
caller via the new ``app.mcp.client`` module.

BE-S1: the implementation lives in the ``app.services.chat`` package
(``interrupts`` / ``secrets`` / ``conversations`` / ``messages`` / ``usage``
/ ``attachments`` / ``runtime_context``); this module is the compatibility
facade and only re-exports.

Helpers re-exported by this module are imported by the trigger executor and the
conversations router; their public shape (``get_agent_with_tools``,
``build_tools_config``, ``build_effective_prompt``, ``build_agent_skills``) is
preserved to keep those callers thin.
"""

from __future__ import annotations

from app.services.chat.attachments import (
    gc_orphan_attachments as gc_orphan_attachments,
)
from app.services.chat.attachments import (
    link_attachments_to_conversation,
)
from app.services.chat.attachments import (
    link_attachments_to_message as link_attachments_to_message,
)
from app.services.chat.attachments import (
    list_conversation_files as list_conversation_files,
)
from app.services.chat.attachments import (
    resolve_turn_user_message_id as resolve_turn_user_message_id,
)
from app.services.chat.conversations import (
    clear_active_branch_override,
    conversation_title_from_content,
    create_conversation,
    delete_conversation,
    gc_orphan_draft_conversations,
    get_conversation,
    get_owned_conversation,
    get_owned_ui_conversation_with_agent,
    is_agent_owned_by_user,
    list_conversations,
    list_conversations_page,
    list_global_conversations_page,
    mark_conversation_read,
    maybe_set_auto_title,
    promote_draft_conversation,
    touch_conversation,
    update_conversation,
)
from app.services.chat.interrupts import (
    _hydrate_pending_interrupt_tool_calls as _hydrate_pending_interrupt_tool_calls,
)
from app.services.chat.messages import list_messages_from_checkpointer
from app.services.chat.runtime_context import (
    build_agent_skills,
    build_effective_prompt,
    build_tools_config,
    get_agent_with_tools,
    get_owned_conversation_with_agent,
    trigger_blocked_tools_for_agent_tree,
)
from app.services.chat.secrets import (
    _redact_response_tool_calls as _redact_response_tool_calls,
)
from app.services.chat.secrets import (
    collect_conversation_secret_values,
)
from app.services.chat.usage import save_token_usage

__all__ = [
    "build_agent_skills",
    "build_effective_prompt",
    "conversation_title_from_content",
    "build_tools_config",
    "clear_active_branch_override",
    "collect_conversation_secret_values",
    "create_conversation",
    "delete_conversation",
    "gc_orphan_draft_conversations",
    "get_agent_with_tools",
    "get_conversation",
    "get_owned_conversation",
    "get_owned_conversation_with_agent",
    "get_owned_ui_conversation_with_agent",
    "is_agent_owned_by_user",
    "link_attachments_to_conversation",
    "list_conversations",
    "list_conversations_page",
    "list_global_conversations_page",
    "list_messages_from_checkpointer",
    "mark_conversation_read",
    "maybe_set_auto_title",
    "promote_draft_conversation",
    "save_token_usage",
    "touch_conversation",
    "trigger_blocked_tools_for_agent_tree",
    "update_conversation",
]
