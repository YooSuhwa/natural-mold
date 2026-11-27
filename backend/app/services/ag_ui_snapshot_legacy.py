"""Read the public transcript of UI chats written before physical runs existed."""

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import event_names
from app.authz.conversation_scope import require_private_conversation
from app.authz.org_context import OrgContext
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.message_event import MessageEvent
from app.schemas.ag_ui_snapshot import AgUiSnapshotMessageLink
from app.schemas.conversation_history import JsonValue
from app.services.ag_ui_journal import JournalEntry, read_legacy_trace_page
from app.services.ag_ui_journal_legacy import legacy_journal_events
from app.services.ag_ui_snapshot_messages import legacy_user_messages

type MessageRow = tuple[dict[str, Any], AgUiSnapshotMessageLink]
_PAGE_SIZE = 128


@dataclass(frozen=True, slots=True)
class _TranscriptEntry(JournalEntry):
    delegated: bool = False


def _transcript_entry(
    record: MessageEvent, raw: dict[str, JsonValue], ordinal: int, secrets: Collection[str]
) -> JournalEntry | None:
    """The journal has already checked the source identity, namespace and ordinal."""
    data = raw.get("data")
    if not isinstance(data, dict):
        return None
    name = raw.get("event")
    source_id = f"{record.assistant_msg_id}-{ordinal}"
    if raw.get("id") != source_id or (ordinal == 1) != (name == event_names.MESSAGE_START):
        return None
    users: list[dict[str, Any]] = []
    if name == event_names.MESSAGE_START:
        if data.get("id") != record.assistant_msg_id:
            return None
        # The historical UI submitted a string. Expanded file blocks and captions
        # in model input do not establish what the user typed into the composer.
        payload = data.get("input")
        inputs = payload.get("messages") if isinstance(payload, dict) else None
        if payload is not None:
            if not (
                isinstance(inputs, list)
                and len(inputs) == 1
                and isinstance(inputs[0], dict)
                and isinstance(inputs[0].get("content"), str)
            ):
                return None
            users = legacy_user_messages(
                raw,
                conversation_id=record.conversation_id,
                physical_run_id=record.assistant_msg_id,
                secret_values=secrets,
            )
            if not users:
                return None
    if name == event_names.MESSAGE_END:
        events = legacy_journal_events(record, raw, ordinal, secrets)
        if events is None:
            return None
        status = data.get("status", "completed")
        if status not in ("completed", "failed"):
            return None
        return _TranscriptEntry(ordinal, source_id, events, terminal_status=str(status))
    # Early streams coalesced tokens into a single final string. If the turn
    # delegated, that string cannot distinguish parent and child contributions.
    delegated = name in {event_names.TOOL_CALL_START, event_names.TOOL_CALL_RESULT} and (
        data.get("tool_name") == "task"
    )
    return _TranscriptEntry(ordinal, source_id, (), tuple(users), delegated=delegated)


def _is_ordinary(conversation: Conversation) -> bool:
    return (
        conversation.source == "ui"
        and conversation.side_chat_parent_id is None
        and conversation.agent_lifecycle_state != "purged"
        and conversation.active_branch_checkpoint_id is None
    )


async def _turn_messages(
    db: AsyncSession,
    conversation: Conversation,
    context: OrgContext,
    trace_id: UUID,
    generation: UUID,
    secrets: Collection[str],
) -> list[dict[str, Any]]:
    after = 0
    users: list[dict[str, Any]] = []
    assistant: dict[str, Any] | None = None
    delegated = False
    while True:
        conversation = await require_private_conversation(db, conversation.id, context)
        if not _is_ordinary(conversation):
            return []
        page = await read_legacy_trace_page(
            db,
            conversation=conversation,
            physical_run_id=trace_id,
            after=after,
            secret_values=secrets,
            entry_projector=_transcript_entry,
        )
        if page.gap or page.generation != str(generation):
            return []
        for entry in page.entries:
            if entry.terminal_status and entry.ordinal != page.high_water:
                return []
            users.extend(entry.legacy_users)
            delegated |= isinstance(entry, _TranscriptEntry) and entry.delegated
            for event in entry.events:
                # Only the complete, redacted message_end can produce an event.
                data = event["data"]
                if assistant is not None or not isinstance(data.get("content"), str):
                    return []
                assistant = {"id": data["id"], "role": "assistant", "content": data["content"]}
        if not page.has_more:
            break
        after = page.entries[-1].ordinal
    if assistant is not None and not delegated:
        users.append(assistant)
    return users


async def read_unlinked_legacy_transcript(
    db: AsyncSession,
    conversation: Conversation,
    context: OrgContext,
    *,
    source: str,
    secret_values: Collection[str] = (),
) -> list[MessageRow]:
    """Read chronological old turns after the scoped checkpointer proved it has no root."""
    if source != "legacy":
        return []
    result: list[MessageRow] = []
    with db.no_autoflush:
        conversation = await require_private_conversation(db, conversation.id, context)
        if not _is_ordinary(conversation):
            return []
        query = (
            select(MessageEvent.id, MessageEvent.assistant_msg_id, MessageEvent.created_at)
            .where(MessageEvent.conversation_id == conversation.id)
            .order_by(MessageEvent.created_at, MessageEvent.id)
            .limit(_PAGE_SIZE)
        )
        page_query = query
        while rows := (await db.execute(page_query)).all():
            # Writers generated canonical str(uuid4()) trace keys. An arbitrary
            # database string cannot be treated as evidence of a public turn.
            ids: dict[str, UUID] = {}
            for _, key, _ in rows:
                try:
                    ident = UUID(key)
                except ValueError:
                    continue
                if str(ident) == key:
                    ids[key] = ident
            # Exclude even inaccessible physical runs. An inaccessible turn
            # must never reappear through the historical, conversation-only path.
            physical = set(
                await db.scalars(
                    select(ConversationRun.id).where(ConversationRun.id.in_(ids.values()))
                )
            )
            for generation, key, created_at in rows:
                if (ident := ids.get(key)) is None or ident in physical:
                    continue
                messages = await _turn_messages(
                    db, conversation, context, ident, generation, secret_values
                )
                parent_id = None
                for message in messages:
                    link = AgUiSnapshotMessageLink(
                        message_id=message["id"], parent_message_id=parent_id, created_at=created_at
                    )
                    result.append((message, link))
                    if message["role"] == "user":
                        parent_id = message["id"]
            last = rows[-1]
            page_query = query.where(
                tuple_(MessageEvent.created_at, MessageEvent.id) > (last.created_at, last.id)
            )
        current = await require_private_conversation(db, conversation.id, context)
        return result if _is_ordinary(current) else []
