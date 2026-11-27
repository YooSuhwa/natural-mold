"""Text previously delivered to the owner, for display of private compaction archives."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import JSON, String, cast, column, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.agent_runtime.protocol_redaction import REDACTED_SENSITIVE_FIELD
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.message_event import MessageEvent, MessageEventChunk

# Keep one extra character so the evidence view can report truncation.
_PUBLIC_PREFIX = 1501
_INPUT_PREVIEW = 500


@dataclass(slots=True)
class ArchivePublicText:
    user_previews: set[str] = field(default_factory=set)
    assistant_messages: dict[str, str | None] = field(default_factory=dict)

    def approved_excerpt(
        self, role: Literal["user", "assistant"], message_id: str | None, archive_text: str
    ) -> str | None:
        if role == "user":
            preview = archive_text[:_INPUT_PREVIEW]
            return preview if preview and preview in self.user_previews else None
        public = self.assistant_messages.get(message_id) if message_id else None
        if not public:
            return None
        if public == archive_text or (
            len(public) == _PUBLIC_PREFIX and archive_text.startswith(public)
        ):
            return public
        # Only the write-time redacted copy can be returned. Validate its visible
        # anchors in sequence so reusing a different message's ID is not evidence.
        parts = public.split(REDACTED_SENSITIVE_FIELD)
        if len(parts) == 1 or not archive_text.startswith(parts[0]):
            return None
        offset = len(parts[0])
        for part in parts[1:]:
            pos = archive_text.find(part, offset)
            if pos < 0:
                return None
            offset = pos + len(part)
        if len(public) < _PUBLIC_PREFIX and not archive_text.endswith(parts[-1]):
            return None
        return public


def _scope(conversation: Conversation) -> tuple:
    return (
        ConversationRun.conversation_id == conversation.id,
        ConversationRun.user_id == conversation.user_id,
        ConversationRun.tenant_id == conversation.tenant_id,
        ConversationRun.org_id == conversation.org_id,
    )


def _boundaries(
    db: AsyncSession,
    conversation: Conversation,
    source: type[MessageEvent] | type[MessageEventChunk],
) -> Select:
    # Filter on the database. Trace batches also contain tool bodies, many deltas
    # and checkpoints, none of which the evidence reader needs to load.
    expand = func.json_each if db.get_bind().dialect.name == "sqlite" else func.json_array_elements
    elements = expand(source.events).table_valued(column("value", JSON))
    event = elements.c.value
    data = event["data"]
    metadata = data["metadata"]
    kind = data["event"].as_string()
    content = data["content"]
    public_text = func.substr(content["text"].as_string(), 1, _PUBLIC_PREFIX)
    run_id = source.assistant_msg_id
    return (
        select(
            run_id,
            metadata["run_id"].as_string(),
            kind,
            data["id"].as_string(),
            data["role"].as_string(),
            data["index"].as_integer(),
            public_text,
        )
        .select_from(source)
        .join(
            ConversationRun,
            func.replace(run_id, "-", "")
            == func.replace(cast(ConversationRun.id, String), "-", ""),
        )
        .join(elements, true())
        .where(
            *_scope(conversation),
            source.conversation_id == conversation.id,
            event["run_id"].as_string() == run_id,
            event["thread_id"].as_string() == str(conversation.id),
            event["method"].as_string() == "messages",
            event["namespace"].as_string() == "[]",
            metadata["langgraph_node"].as_string() == "model",
            metadata["lc_source"].as_string().is_(None),
            metadata["run_id"].as_string().is_not(None),
            (kind.in_(("message-start", "message-finish")))
            | ((kind == "content-block-finish") & (content["type"].as_string() == "text")),
        )
        .order_by(run_id, event["seq"].as_integer())
    )


@dataclass(slots=True)
class _PublishedMessage:
    message_id: str
    text: str = ""
    last_index: int = -1


async def load_archive_public_text(
    db: AsyncSession, conversation: Conversation
) -> ArchivePublicText:
    result = ArchivePublicText()
    previews = await db.stream_scalars(
        select(ConversationRun.input_preview).where(
            *_scope(conversation),
            ConversationRun.source.in_(("start", "chat", "edit")),
            ConversationRun.input_preview.is_not(None),
        )
    )
    async for preview in previews:
        if preview:
            result.user_previews.add(preview)
    pending: dict[tuple[str, str], _PublishedMessage] = {}
    # Legacy header batches precede subsequently appended chunk batches in replay.
    for source in (MessageEvent, MessageEventChunk):
        rows = await db.stream(
            _boundaries(db, conversation, source).execution_options(yield_per=100)
        )
        async for run_id, provider_call, kind, message_id, role, index, text in rows:
            key = (run_id, provider_call)
            if kind == "message-start":
                pending.pop(key, None)
                if role == "ai" and isinstance(message_id, str) and message_id:
                    pending[key] = _PublishedMessage(message_id)
                continue
            message = pending.get(key)
            if message is None:
                continue
            if kind == "content-block-finish":
                if (
                    not isinstance(index, int)
                    or index <= message.last_index
                    or not isinstance(text, str)
                ):
                    del pending[key]
                    continue
                message.last_index = index
                message.text += text[: _PUBLIC_PREFIX - len(message.text)]
            elif kind == "message-finish":
                del pending[key]
                previous = result.assistant_messages.setdefault(message.message_id, message.text)
                if previous != message.text:
                    result.assistant_messages[message.message_id] = None
    return result
