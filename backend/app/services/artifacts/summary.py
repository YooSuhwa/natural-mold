"""Artifact summaries and event payloads (BE-S8 split).

Summary/version hydration helpers shared by the recorder (file events) and
the library (list/read endpoints), plus the owned-artifact summary getters.
``FileEventOperation`` lives here (leaf position) because both ``recorder``
and this module need it and ``recorder`` already imports from this module.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Literal, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.schemas.artifact import (
    ArtifactKind,
    ArtifactStatus,
    ArtifactSummary,
    FileEventPayload,
)
from app.services.artifacts.errors import ArtifactNotFoundError

FileEventOperation = Literal["created", "updated", "deleted", "failed"]


async def get_conversation_artifact_summary(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    artifact_id: uuid.UUID | str,
) -> ArtifactSummary:
    # Deferred import: library imports this module at top level (summaries for
    # list endpoints), so the reverse edge must stay function-local.
    from app.services.artifacts.library import _get_owned_artifact

    artifact = await _get_owned_artifact(
        db,
        user_id=user_id,
        artifact_id=artifact_id,
        conversation_id=conversation_id,
    )
    return await _summary_from_artifact(db, artifact, include_names=True)


async def get_artifact_summary(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    artifact_id: uuid.UUID | str,
) -> ArtifactSummary:
    # Deferred import: see get_conversation_artifact_summary.
    from app.services.artifacts.library import _get_owned_artifact

    artifact = await _get_owned_artifact(db, user_id=user_id, artifact_id=artifact_id)
    return await _summary_from_artifact(db, artifact, include_names=True)


async def _summaries_from_artifacts(
    db: AsyncSession,
    artifacts: Sequence[ConversationArtifact],
    *,
    include_names: bool = False,
) -> list[ArtifactSummary]:
    if not artifacts:
        return []

    versions_by_artifact_id = await _current_versions_for_artifacts(db, artifacts)
    agent_names: dict[uuid.UUID, str | None] = {}
    conversation_titles: dict[uuid.UUID, str | None] = {}
    if include_names:
        agent_ids = {artifact.agent_id for artifact in artifacts}
        conversation_ids = {artifact.conversation_id for artifact in artifacts}
        if agent_ids:
            agent_rows = await db.execute(
                select(Agent.id, Agent.name).where(Agent.id.in_(agent_ids))
            )
            agent_names = dict(agent_rows.tuples().all())
        if conversation_ids:
            conversation_rows = await db.execute(
                select(Conversation.id, Conversation.title).where(
                    Conversation.id.in_(conversation_ids)
                )
            )
            conversation_titles = dict(conversation_rows.tuples().all())

    summaries: list[ArtifactSummary] = []
    for artifact in artifacts:
        version = versions_by_artifact_id.get(artifact.id)
        if version is None:
            continue
        summaries.append(
            _summary_from_artifact_with_version(
                artifact,
                version,
                agent_name=agent_names.get(artifact.agent_id),
                conversation_title=conversation_titles.get(artifact.conversation_id),
            )
        )
    return summaries


async def _current_versions_for_artifacts(
    db: AsyncSession,
    artifacts: Sequence[ConversationArtifact],
) -> dict[uuid.UUID, ArtifactVersion]:
    versions_by_artifact_id: dict[uuid.UUID, ArtifactVersion] = {}
    current_version_ids = [
        artifact.current_version_id
        for artifact in artifacts
        if artifact.current_version_id is not None
    ]
    if current_version_ids:
        current_versions = (
            (
                await db.execute(
                    select(ArtifactVersion).where(ArtifactVersion.id.in_(current_version_ids))
                )
            )
            .scalars()
            .all()
        )
        for version in current_versions:
            versions_by_artifact_id[version.artifact_id] = version

    missing_artifact_ids = [
        artifact.id for artifact in artifacts if artifact.id not in versions_by_artifact_id
    ]
    if missing_artifact_ids:
        fallback_versions = (
            (
                await db.execute(
                    select(ArtifactVersion)
                    .where(ArtifactVersion.artifact_id.in_(missing_artifact_ids))
                    .order_by(ArtifactVersion.version_number.desc())
                )
            )
            .scalars()
            .all()
        )
        for version in fallback_versions:
            versions_by_artifact_id.setdefault(version.artifact_id, version)

    return versions_by_artifact_id


async def _summary_from_artifact(
    db: AsyncSession,
    artifact: ConversationArtifact,
    *,
    include_names: bool = False,
) -> ArtifactSummary:
    version = await _current_version(db, artifact)
    agent_name: str | None = None
    conversation_title: str | None = None
    if include_names:
        agent_name = (
            await db.execute(select(Agent.name).where(Agent.id == artifact.agent_id))
        ).scalar_one_or_none()
        conversation_title = (
            await db.execute(
                select(Conversation.title).where(Conversation.id == artifact.conversation_id)
            )
        ).scalar_one_or_none()

    return _summary_from_artifact_with_version(
        artifact,
        version,
        agent_name=agent_name,
        conversation_title=conversation_title,
    )


def _summary_from_artifact_with_version(
    artifact: ConversationArtifact,
    version: ArtifactVersion,
    *,
    agent_name: str | None = None,
    conversation_title: str | None = None,
) -> ArtifactSummary:
    return ArtifactSummary(
        id=artifact.id,
        agent_id=artifact.agent_id,
        conversation_id=artifact.conversation_id,
        assistant_msg_id=artifact.assistant_msg_id,
        run_id=artifact.assistant_msg_id,
        tool_call_id=artifact.tool_call_id,
        source_tool_name=artifact.source_tool_name,
        path=artifact.logical_path,
        display_name=artifact.display_name,
        mime_type=artifact.mime_type,
        extension=artifact.extension,
        artifact_kind=cast(ArtifactKind, artifact.artifact_kind),
        size_bytes=artifact.size_bytes,
        sha256=artifact.sha256,
        status=cast(ArtifactStatus, artifact.status),
        is_favorite=artifact.is_favorite,
        last_opened_at=artifact.last_opened_at,
        preview_count=artifact.preview_count,
        download_count=artifact.download_count,
        version_id=version.id,
        version_number=version.version_number,
        created_at=artifact.created_at,
        updated_at=artifact.updated_at,
        agent_name=agent_name,
        conversation_title=conversation_title,
        url=f"/api/conversations/{artifact.conversation_id}/artifacts/{artifact.id}",
        preview_url=f"/api/conversations/{artifact.conversation_id}/artifacts/{artifact.id}/content",
        download_url=f"/api/conversations/{artifact.conversation_id}/artifacts/{artifact.id}/download",
        linked_message_ids=artifact.linked_message_ids,
    )


async def _current_version(db: AsyncSession, artifact: ConversationArtifact) -> ArtifactVersion:
    version: ArtifactVersion | None = None
    if artifact.current_version_id is not None:
        version = await db.get(ArtifactVersion, artifact.current_version_id)
    if version is None:
        version = (
            await db.execute(
                select(ArtifactVersion)
                .where(ArtifactVersion.artifact_id == artifact.id)
                .order_by(ArtifactVersion.version_number.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if version is None:
        raise ArtifactNotFoundError("artifact version not found")
    return version


def _file_event_payload(op: FileEventOperation, summary: ArtifactSummary) -> FileEventPayload:
    return FileEventPayload(op=op, **summary.model_dump())


def is_text_preview_artifact(summary: ArtifactSummary) -> bool:
    if summary.mime_type.startswith("text/"):
        return True
    if summary.artifact_kind in {"markdown", "code", "html"}:
        return True
    return summary.extension in {"csv", "tsv", "json", "txt", "log", "yaml", "yml", "toml"}
