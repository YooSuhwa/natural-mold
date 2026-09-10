from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.models.conversation import Conversation
from app.schemas.chat_resource_context import (
    MAX_RESOURCE_CONTEXT_ITEM_BYTES,
    MAX_RESOURCE_CONTEXT_TOTAL_BYTES,
    ChatResourceContextRequest,
    ChatResourceReference,
    FrozenChatResource,
    FrozenChatResourceContext,
)
from app.services.artifact_storage import ArtifactStorageBackend, get_artifact_storage_backend
from app.services.chat_quote_context import freeze_message_quote
from app.services.chat_resource_context_errors import ResourceContextLimitError
from app.services.chat_resource_context_sources import (
    ArtifactSourceRequest,
    LoadedResource,
    ResourceContextScope,
    load_artifact_source,
    load_file_source,
    load_skill_source,
    owned_conversation,
)


@dataclass(frozen=True, slots=True)
class ConversationTranscript:
    text: str
    checkpoint_id: str | None


@dataclass(frozen=True, slots=True)
class ResourceContextDependencies:
    artifact_storage: ArtifactStorageBackend | None = None


class ChatResourceContextResolver:
    """Authorize resource identifiers and freeze bounded user-context snapshots."""

    def __init__(
        self,
        db: AsyncSession,
        scope: ResourceContextScope,
        dependencies: ResourceContextDependencies | None = None,
    ) -> None:
        self._db = db
        self._scope = scope
        self._dependencies = dependencies or ResourceContextDependencies()

    async def resolve(self, request: ChatResourceContextRequest) -> FrozenChatResourceContext:
        resources: list[FrozenChatResource] = []
        total_bytes = 0
        for reference in request.resources:
            frozen = await self._resolve_one(reference)
            total_bytes += len(frozen.text.encode("utf-8"))
            if total_bytes > MAX_RESOURCE_CONTEXT_TOTAL_BYTES:
                raise ResourceContextLimitError(total_bytes, MAX_RESOURCE_CONTEXT_TOTAL_BYTES)
            resources.append(frozen)
        return FrozenChatResourceContext(resources=resources)

    async def reauthorize(self, context: FrozenChatResourceContext) -> FrozenChatResourceContext:
        """Recheck access without refreshing or replacing stored snapshot text."""

        for resource in context.resources:
            reference = ChatResourceReference(
                kind=resource.kind,
                id=resource.id,
                version_id=resource.version_id,
                label=resource.label,
                message_id=resource.message_id,
                quote=resource.quote,
                comment=resource.comment,
                message_role=resource.message_role,
            )
            match resource.kind:
                case "file":
                    await load_file_source(self._db, self._scope, reference)
                case "artifact":
                    await load_artifact_source(
                        self._db,
                        self._scope,
                        ArtifactSourceRequest(
                            reference=reference,
                            storage=self._artifact_storage(),
                        ),
                    )
                case "skill":
                    await load_skill_source(self._db, self._scope, reference)
                case "conversation":
                    await owned_conversation(self._db, self._scope, resource.id)
                case unreachable:
                    assert_never(unreachable)
        return context

    async def _resolve_one(self, reference: ChatResourceReference) -> FrozenChatResource:
        match reference.kind:
            case "file":
                return await self._freeze_loaded(
                    await load_file_source(self._db, self._scope, reference)
                )
            case "artifact":
                return await self._freeze_loaded(
                    await load_artifact_source(
                        self._db,
                        self._scope,
                        ArtifactSourceRequest(
                            reference=reference,
                            storage=self._artifact_storage(),
                        ),
                    )
                )
            case "skill":
                return await self._freeze_loaded(
                    await load_skill_source(self._db, self._scope, reference)
                )
            case "conversation":
                conversation = await owned_conversation(self._db, self._scope, reference.id)
                if reference.message_id is not None:
                    return await freeze_message_quote(conversation, reference)
                transcript = await _conversation_transcript(conversation)
                text = _truncate_utf8(transcript.text, MAX_RESOURCE_CONTEXT_ITEM_BYTES)
                digest = hashlib.sha256(text.encode()).hexdigest()
                source_version = (
                    f"checkpoint:{transcript.checkpoint_id}"
                    if transcript.checkpoint_id is not None
                    else f"sha256:{digest}"
                )
                return FrozenChatResource(
                    kind=reference.kind,
                    id=reference.id,
                    label=reference.label,
                    resolved_label=conversation.title or "Untitled conversation",
                    mime_type="text/plain",
                    source_version=source_version,
                    content_sha256=digest,
                    text=text,
                )
            case unreachable:
                assert_never(unreachable)

    async def _freeze_loaded(self, loaded: LoadedResource) -> FrozenChatResource:
        content_available = loaded.path is not None
        if loaded.path is not None:
            raw = await run_in_threadpool(
                _read_prefix,
                loaded.path,
                MAX_RESOURCE_CONTEXT_ITEM_BYTES + 1,
            )
            text = _truncate_utf8(
                raw.decode("utf-8", errors="replace"), MAX_RESOURCE_CONTEXT_ITEM_BYTES
            )
        else:
            text = (
                f"Binary content unavailable; name={loaded.resolved_label}; "
                f"mime={loaded.mime_type}; size={loaded.binary_size or 0}; "
                f"sha256={loaded.content_sha256}"
            )
        return FrozenChatResource(
            kind=loaded.reference.kind,
            id=loaded.reference.id,
            version_id=loaded.reference.version_id,
            label=loaded.reference.label,
            resolved_label=loaded.resolved_label,
            mime_type=loaded.mime_type,
            source_version=loaded.source_version,
            content_sha256=loaded.content_sha256,
            text=text,
            content_available=content_available,
        )

    def _artifact_storage(self) -> ArtifactStorageBackend:
        return self._dependencies.artifact_storage or get_artifact_storage_backend()


async def _conversation_transcript(conversation: Conversation) -> ConversationTranscript:
    from app.agent_runtime.checkpointer import get_checkpointer
    from app.services.thread_branch_service import build_message_tree

    tree = await build_message_tree(
        get_checkpointer(),
        str(conversation.id),
        active_checkpoint_id=conversation.active_branch_checkpoint_id,
    )
    lines: list[str] = []
    for node in tree.nodes:
        message_type = getattr(node.message, "type", None)
        content = getattr(node.message, "content", "")
        if message_type in {"human", "ai"} and isinstance(content, str):
            role = "user" if message_type == "human" else "assistant"
            lines.append(f"{role}: {content}")
    return ConversationTranscript(text="\n".join(lines), checkpoint_id=tree.active_checkpoint_id)


def _read_prefix(path: Path, byte_count: int) -> bytes:
    with path.open("rb") as file_obj:
        return file_obj.read(byte_count)


def _truncate_utf8(value: str, maximum_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= maximum_bytes:
        return value
    return encoded[:maximum_bytes].decode("utf-8", errors="ignore")


__all__ = [
    "ChatResourceContextResolver",
    "ConversationTranscript",
    "ResourceContextDependencies",
    "ResourceContextScope",
]
