from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.models.message_attachment import MessageAttachment
from app.models.skill import AgentSkillLink, Skill
from app.schemas.chat_resource_context import ChatResourceReference
from app.services.artifact_storage import ArtifactStorageBackend
from app.services.chat_resource_context_errors import ResourceContextNotFoundError
from app.storage.paths import resolve_data_path

_TEXTUAL_MIME_EXACT: Final = {"application/json", "application/xml"}


@dataclass(frozen=True, slots=True)
class ResourceContextScope:
    user_id: uuid.UUID
    agent_id: uuid.UUID
    conversation_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class LoadedResource:
    reference: ChatResourceReference
    resolved_label: str
    mime_type: str
    source_version: str
    content_sha256: str
    path: Path | None
    binary_size: int | None = None


@dataclass(frozen=True, slots=True)
class ArtifactSourceRequest:
    reference: ChatResourceReference
    storage: ArtifactStorageBackend


def is_textual_mime(mime_type: str) -> bool:
    normalized = mime_type.split(";", 1)[0].strip().lower()
    return normalized.startswith("text/") or normalized in _TEXTUAL_MIME_EXACT


async def load_file_source(
    db: AsyncSession,
    scope: ResourceContextScope,
    reference: ChatResourceReference,
) -> LoadedResource:
    attachment = await db.scalar(
        select(MessageAttachment)
        .join(Conversation, Conversation.id == MessageAttachment.conversation_id)
        .join(Agent, Agent.id == Conversation.agent_id)
        .where(
            MessageAttachment.id == reference.id,
            MessageAttachment.user_id == scope.user_id,
            Agent.user_id == scope.user_id,
        )
    )
    if attachment is None:
        raise ResourceContextNotFoundError()
    path = Path(attachment.storage_path)
    if not await run_in_threadpool(path.is_file):
        raise ResourceContextNotFoundError()
    digest = await run_in_threadpool(_sha256_path, path)
    return LoadedResource(
        reference=reference,
        resolved_label=attachment.filename,
        mime_type=attachment.mime_type,
        source_version=f"sha256:{digest}",
        content_sha256=digest,
        path=path if is_textual_mime(attachment.mime_type) else None,
        binary_size=attachment.size_bytes,
    )


async def load_artifact_source(
    db: AsyncSession,
    scope: ResourceContextScope,
    request: ArtifactSourceRequest,
) -> LoadedResource:
    reference = request.reference
    artifact = await db.scalar(
        select(ConversationArtifact).where(
            ConversationArtifact.id == reference.id,
            ConversationArtifact.user_id == scope.user_id,
            ConversationArtifact.status == "ready",
        )
    )
    if artifact is None:
        raise ResourceContextNotFoundError()
    version = await _artifact_version(db, artifact, reference.version_id)
    try:
        path = await request.storage.local_path(object_key=version.object_key)
    except (FileNotFoundError, ValueError) as exc:
        raise ResourceContextNotFoundError() from exc
    exact_reference = reference.model_copy(update={"version_id": version.id})
    return LoadedResource(
        reference=exact_reference,
        resolved_label=artifact.display_name,
        mime_type=artifact.mime_type,
        source_version=f"artifact-version:{version.id}",
        content_sha256=version.sha256,
        path=path if is_textual_mime(artifact.mime_type) else None,
        binary_size=version.size_bytes,
    )


async def load_skill_source(
    db: AsyncSession,
    scope: ResourceContextScope,
    reference: ChatResourceReference,
) -> LoadedResource:
    skill = await db.scalar(
        select(Skill)
        .join(AgentSkillLink, AgentSkillLink.skill_id == Skill.id)
        .where(
            Skill.id == reference.id,
            Skill.user_id == scope.user_id,
            AgentSkillLink.agent_id == scope.agent_id,
        )
    )
    path = _skill_entry_path(skill)
    if skill is None or path is None or not await run_in_threadpool(path.is_file):
        raise ResourceContextNotFoundError()
    digest = skill.content_hash or await run_in_threadpool(_sha256_path, path)
    source_version = (
        f"skill-revision:{skill.current_revision_id}"
        if skill.current_revision_id is not None
        else f"sha256:{digest}"
    )
    return LoadedResource(
        reference=reference,
        resolved_label=skill.name,
        mime_type="text/markdown",
        source_version=source_version,
        content_sha256=digest,
        path=path,
    )


async def owned_conversation(
    db: AsyncSession,
    scope: ResourceContextScope,
    conversation_id: uuid.UUID,
) -> Conversation:
    conversation = await db.scalar(
        select(Conversation)
        .join(Agent, Agent.id == Conversation.agent_id)
        .where(Conversation.id == conversation_id, Agent.user_id == scope.user_id)
    )
    if conversation is None:
        raise ResourceContextNotFoundError()
    return conversation


async def _artifact_version(
    db: AsyncSession,
    artifact: ConversationArtifact,
    requested_version_id: uuid.UUID | None,
) -> ArtifactVersion:
    version_id = requested_version_id or artifact.current_version_id
    version = await db.get(ArtifactVersion, version_id) if version_id is not None else None
    if version is None and requested_version_id is None:
        version = await db.scalar(
            select(ArtifactVersion)
            .where(ArtifactVersion.artifact_id == artifact.id)
            .order_by(ArtifactVersion.version_number.desc())
            .limit(1)
        )
    if version is None or version.artifact_id != artifact.id:
        raise ResourceContextNotFoundError()
    return version


def _skill_entry_path(skill: Skill | None) -> Path | None:
    if skill is None or skill.storage_path is None:
        return None
    root = resolve_data_path(skill.storage_path)
    return root / "SKILL.md" if skill.kind == "package" else root


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "ArtifactSourceRequest",
    "LoadedResource",
    "ResourceContextScope",
    "is_textual_mime",
    "load_artifact_source",
    "load_file_source",
    "load_skill_source",
    "owned_conversation",
]
