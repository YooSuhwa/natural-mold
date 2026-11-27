"""Owner-visible evidence from an exact root checkpoint's verified compaction archive."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, get_buffer_string
from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.conversation_checkpointer import loaded_conversation_checkpointer
from app.agent_runtime.history_archive import (
    history_archive_checkpoint_matches,
    iter_history_archive_bytes,
    iter_json_lines,
)
from app.agent_runtime.message_utils import content_to_text
from app.agent_runtime.offload_storage_io import read_rollback_history, read_scoped_regular_file
from app.agent_runtime.offload_storage_types import (
    ARCHIVE_FILE,
    PHYSICAL_ROOT,
    ROLLBACK_SESSION_FILE,
    SESSION_FILE,
    VIRTUAL_ROOT,
    OffloadKind,
    OffloadSecurityError,
    logical_offload_id,
    token,
)
from app.agent_runtime.protocol_egress import project_and_redact_protocol_data
from app.agent_runtime.runtime_config import runtime_data_dir
from app.authz.org_context import OrgContext
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.services.conversation_archive_public_text import (
    ArchivePublicText,
    load_archive_public_text,
)
from app.services.conversation_history_secrets import collect_strict_history_secrets
from app.services.thread_branch_service import materialize_messages_at_checkpoint


class EvidenceUnavailable(ValueError):
    """No verified archive is available to this reader at the requested checkpoint."""


class EvidenceDigestChanged(ValueError):
    """The legacy file has changed since the viewer pinned its digest."""


class EvidenceExcerpt(BaseModel):
    role: Literal["user", "assistant"]
    text: str
    truncated: bool


class HistoryEvidence(BaseModel):
    checkpoint_id: str
    history_id: str
    cutoff_index: int | None
    format: Literal["jsonl", "legacy_markdown"]
    content_sha256: str = Field(
        description=(
            "SHA-256 of verified JSONL records or of one legacy Markdown file, according to "
            "format. "
            "JSONL excludes a legacy predecessor."
        )
    )
    byte_length: int
    excerpts: list[EvidenceExcerpt] = Field(
        description=(
            "Previously published owner-visible text. User entries are limited to submitted run "
            "previews; assistant entries require a matching published final text block."
        )
    )
    next_offset: int | None


@dataclass(frozen=True)
class EvidencePage:
    offset: int
    limit: int
    expected_sha256: str | None = None


@dataclass(frozen=True)
class _Reference:
    leaf: str
    kind: Literal["jsonl", "scoped_markdown", "legacy_markdown", "rollback_markdown"]
    directory: tuple[str, ...]
    receipt_leaf: str | None = None


def _history_reference(
    values: Mapping, conversation: Conversation, actor_owner: UUID
) -> _Reference:
    event = values.get("_summarization_event")
    if not isinstance(event, Mapping) or not isinstance(event.get("file_path"), str):
        raise EvidenceUnavailable
    path = event["file_path"]
    owner = token("owner", str(actor_owner))
    scope = token("conversation", str(conversation.id))
    actor = token("actor", str(conversation.agent_id))
    prefix = f"{VIRTUAL_ROOT}/{owner}/{scope}/{actor}/conversation_history/"
    if path.startswith(prefix):
        leaf = path[len(prefix) :]
        if ARCHIVE_FILE.fullmatch(leaf):
            return _Reference(leaf, "jsonl", ())
        if ROLLBACK_SESSION_FILE.fullmatch(leaf):
            return _Reference(leaf, "rollback_markdown", ())
        if SESSION_FILE.fullmatch(leaf):
            return _Reference(
                token("history-path", leaf),
                "scoped_markdown",
                (PHYSICAL_ROOT, "offload", "history", owner, scope),
            )
    if path.startswith("/conversation_history/"):
        leaf = path.removeprefix("/conversation_history/")
        if ROLLBACK_SESSION_FILE.fullmatch(leaf):
            raise EvidenceUnavailable
        session = values.get("_summarization_session_id")
        if leaf == f"{conversation.id}.md":
            return _Reference(leaf, "legacy_markdown", ("conversation_history",))
        if SESSION_FILE.fullmatch(leaf) and isinstance(session, str) and leaf == f"{session}.md":
            # The flat session directory has no owner namespace. A client-writable
            # session id is insufficient; use the verified, owner-scoped migrated copy.
            return _Reference(
                token("history-path", leaf),
                "scoped_markdown",
                (PHYSICAL_ROOT, "offload", "history", owner, scope),
                token("migration-receipt", f"session:{leaf}") + ".json",
            )
    raise EvidenceUnavailable


def _public_text(
    message: Mapping, secrets: set[str], published: ArchivePublicText
) -> EvidenceExcerpt | None:
    role = message.get("type")
    if role not in ("human", "ai") or message.get("name"):
        return None
    kwargs = message.get("additional_kwargs")
    if isinstance(kwargs, Mapping) and kwargs.get("lc_source") == "summarization":
        return None
    content = message.get("content")
    if not isinstance(content, str | list):
        return None
    text = content_to_text(content)
    if not text.strip():
        return None
    display_role: Literal["user", "assistant"] = "user" if role == "human" else "assistant"
    identifier = message.get("id")
    source = published.approved_excerpt(
        display_role, identifier if isinstance(identifier, str) else None, text
    )
    if source is None:
        return None
    redacted = project_and_redact_protocol_data("messages", source, secret_values=secrets)
    if not isinstance(redacted, str) or not redacted:
        return None
    # Human previews are the owner's already-visible submitted input (max 500
    # chars). Only assistant text has write-time and current-secret redaction.
    return EvidenceExcerpt(
        role=display_role,
        text=redacted[:1500],
        truncated=len(redacted) > 1500
        or (len(source) == 500 and len(text) > len(source) and display_role == "user"),
    )


def _legacy_messages(raw: bytes, values: Mapping) -> Iterator[dict]:
    # A session Markdown file was mutable across checkpoints. Only quote messages
    # confirmed by this checkpoint *and* its archived XML. Never render raw Markdown.
    messages = values.get("messages", ())
    event = values["_summarization_event"]
    cutoff = event.get("cutoff_index")
    if type(cutoff) is not int or cutoff < 0 or not isinstance(messages, Sequence):
        return
    for index, message in enumerate(messages):
        if index >= cutoff or (
            event.get("cutoff_message_id")
            and isinstance(message, BaseMessage)
            and message.id == event["cutoff_message_id"]
        ):
            break
        if (
            isinstance(message, (AIMessage, HumanMessage))
            and not message.name
            and get_buffer_string([message], format="xml").encode() in raw
        ):
            yield message.model_dump(mode="json")


def _read_evidence(
    data_dir: Path,
    conversation: Conversation,
    checkpoint_id: str,
    values: Mapping,
    actor_owner: UUID,
    introducing_parent: str,
    published: ArchivePublicText,
    secrets: set[str],
    page: EvidencePage,
) -> HistoryEvidence:
    ref = _history_reference(values, conversation, actor_owner)
    digest = hashlib.sha256()
    size = 0
    if conversation.agent_id is None:
        raise EvidenceUnavailable
    if ref.kind == "jsonl":
        if not history_archive_checkpoint_matches(
            data_dir,
            owner_id=str(conversation.user_id),
            conversation_id=str(conversation.id),
            actor_id=conversation.agent_id,
            leaf=ref.leaf,
            checkpoint_id=introducing_parent,
        ):
            raise EvidenceUnavailable
        stream = iter_history_archive_bytes(
            data_dir,
            owner_id=str(conversation.user_id),
            conversation_id=str(conversation.id),
            actor_id=conversation.agent_id,
            leaf=ref.leaf,
        )

        def counted() -> Iterator[bytes]:
            nonlocal size
            for block in stream:
                size += len(block)
                digest.update(block)
                yield block

        records = iter_json_lines(counted())
    else:
        if ref.kind == "rollback_markdown":
            raw = read_rollback_history(
                data_dir,
                owner_id=str(conversation.user_id),
                legacy_owner_id=str(actor_owner),
                conversation_id=str(conversation.id),
                actor_id=conversation.agent_id,
                leaf=ref.leaf,
            )
        else:
            raw = read_scoped_regular_file(data_dir, (*ref.directory, ref.leaf))
        digest.update(raw)
        size = len(raw)
        if ref.receipt_leaf:
            receipt = json.loads(
                read_scoped_regular_file(data_dir, (*ref.directory, "receipts", ref.receipt_leaf))
            )
            if (
                not isinstance(receipt, dict)
                or receipt.get("source_kind") != "session"
                or receipt.get("content_sha256") != digest.hexdigest()
                or receipt.get("size") != size
            ):
                raise EvidenceUnavailable
        records = _legacy_messages(raw, values)
    excerpts: list[EvidenceExcerpt] = []
    next_offset = None
    index = 0
    for record in records:
        # Keep reading to verify the complete archive even after filling the page.
        if next_offset is not None:
            continue
        message = json.loads(record) if isinstance(record, bytes) else record
        if not isinstance(message, dict):
            raise EvidenceUnavailable
        excerpt = _public_text(message, secrets, published)
        if excerpt is None:
            continue
        if index >= page.offset + page.limit:
            next_offset = index
        elif index >= page.offset:
            excerpts.append(excerpt)
        index += 1
    sha256 = digest.hexdigest()
    if page.expected_sha256 is not None and page.expected_sha256 != sha256:
        raise EvidenceDigestChanged
    cutoff = values["_summarization_event"].get("cutoff_index")
    return HistoryEvidence(
        checkpoint_id=checkpoint_id,
        cutoff_index=cutoff if type(cutoff) is int and cutoff >= 0 else None,
        history_id=logical_offload_id(
            OffloadKind.HISTORY, values["_summarization_event"]["file_path"]
        ),
        format="jsonl" if ref.kind == "jsonl" else "legacy_markdown",
        content_sha256=sha256,
        byte_length=size,
        excerpts=excerpts,
        next_offset=next_offset,
    )


def _event_identity(checkpoint: CheckpointTuple) -> tuple | None:
    values = checkpoint.checkpoint.get("channel_values", {})
    event = values.get("_summarization_event")
    if not isinstance(event, Mapping) or not isinstance(event.get("file_path"), str):
        return None
    return (
        event["file_path"],
        event.get("cutoff_index"),
        event.get("cutoff_message_id"),
        event.get("created_at_index"),
    )


async def _exact_checkpoint(
    saver: BaseCheckpointSaver, thread_id: str, checkpoint_id: str
) -> CheckpointTuple:
    config = {"thread_id": thread_id, "checkpoint_ns": "", "checkpoint_id": checkpoint_id}
    checkpoint = await saver.aget_tuple({"configurable": config})
    if checkpoint is None:
        raise EvidenceUnavailable
    returned = checkpoint.config.get("configurable", {})
    if any(returned.get(key) != value for key, value in config.items()):
        raise EvidenceUnavailable
    return checkpoint


async def _introducing_checkpoint(
    saver: BaseCheckpointSaver, thread_id: str, checkpoint: CheckpointTuple
) -> tuple[str, CheckpointTuple]:
    # API state updates may set arbitrary channels. Follow *this branch* to
    # the graph loop that first produced the reference, ignoring copied values.
    identity = _event_identity(checkpoint)
    seen: set[str] = set()
    while identity:
        config = (checkpoint.parent_config or {}).get("configurable", {})
        parent_id = config.get("checkpoint_id")
        if (
            not isinstance(parent_id, str)
            or parent_id in seen
            or config.get("thread_id") != thread_id
            or config.get("checkpoint_ns", "") != ""
        ):
            break
        seen.add(parent_id)
        parent = await _exact_checkpoint(saver, thread_id, parent_id)
        if _event_identity(parent) != identity:
            if checkpoint.metadata.get("source") == "loop":
                return parent_id, checkpoint
            break
        checkpoint = parent
    raise EvidenceUnavailable


async def read_history_evidence(
    db: AsyncSession,
    conversation: Conversation,
    context: OrgContext,
    checkpoint_id: str,
    page: EvidencePage,
) -> HistoryEvidence:
    if conversation.agent_id is None:
        raise EvidenceUnavailable
    saver = await loaded_conversation_checkpointer(conversation, context, db=db)
    actor_owner = await db.scalar(
        select(Agent.user_id).where(
            Agent.id == conversation.agent_id, Agent.tenant_id == conversation.tenant_id
        )
    )
    if actor_owner is None:
        raise EvidenceUnavailable
    thread_id = str(conversation.id)
    checkpoint = await _exact_checkpoint(saver, thread_id, checkpoint_id)
    values = checkpoint.checkpoint.get("channel_values")
    if not isinstance(values, Mapping):
        raise EvidenceUnavailable
    ref = _history_reference(values, conversation, actor_owner)
    introducing_parent, origin = await _introducing_checkpoint(saver, thread_id, checkpoint)
    values = origin.checkpoint["channel_values"]
    if ref.kind != "jsonl" and not isinstance(values.get("messages"), list):
        # DeltaChannel checkpoints can omit the message list or wrap its snapshot.
        # Reconstruct only the trusted originating branch before matching legacy XML.
        origin_id = origin.config.get("configurable", {})["checkpoint_id"]
        messages = await materialize_messages_at_checkpoint(saver, thread_id, origin_id)
        values = {**values, "messages": messages}
    published = await load_archive_public_text(db, conversation)
    secrets = await collect_strict_history_secrets(db, conversation.agent_id, conversation.id)
    try:
        return await asyncio.to_thread(
            _read_evidence,
            runtime_data_dir(),
            conversation,
            checkpoint_id,
            values,
            actor_owner,
            introducing_parent,
            published,
            secrets,
            page,
        )
    except (
        OffloadSecurityError,
        FileNotFoundError,
        ValidationError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        raise EvidenceUnavailable from exc
