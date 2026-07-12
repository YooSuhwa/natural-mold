"""Artifact library queries (BE-S8 split).

Read-path CRUD: conversation/library listings, cursor pagination, favorites,
open/download counters, stats, soft delete, and the owned-artifact lookups.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.models.message_event import MessageEvent
from app.schemas.artifact import (
    ArtifactKindStat,
    ArtifactLibraryPage,
    ArtifactLibraryStats,
    ArtifactSummary,
)
from app.services.artifacts.errors import ArtifactNotFoundError
from app.services.artifacts.summary import (
    _current_version,
    _summaries_from_artifacts,
    _summary_from_artifact,
)

LIBRARY_CURSOR_SEPARATOR = "|"


def _normalize_cursor_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _encode_library_cursor(artifact: ConversationArtifact) -> str:
    return f"{artifact.created_at.isoformat()}{LIBRARY_CURSOR_SEPARATOR}{artifact.id}"


def _decode_library_cursor(cursor: str) -> tuple[datetime, uuid.UUID | None] | None:
    if LIBRARY_CURSOR_SEPARATOR in cursor:
        raw_datetime, raw_id = cursor.rsplit(LIBRARY_CURSOR_SEPARATOR, 1)
        try:
            cursor_dt = _normalize_cursor_datetime(datetime.fromisoformat(raw_datetime))
            return cursor_dt, uuid.UUID(raw_id)
        except ValueError:
            return None
    try:
        return _normalize_cursor_datetime(datetime.fromisoformat(cursor)), None
    except ValueError:
        return None


async def list_conversation_artifacts(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> list[ArtifactSummary]:
    artifacts = (
        (
            await db.execute(
                select(ConversationArtifact)
                .where(
                    ConversationArtifact.user_id == user_id,
                    ConversationArtifact.conversation_id == conversation_id,
                    ConversationArtifact.status != "deleted",
                )
                .order_by(
                    ConversationArtifact.created_at.desc(),
                    ConversationArtifact.display_name.asc(),
                )
            )
        )
        .scalars()
        .all()
    )
    return await _summaries_from_artifacts(db, artifacts)


async def list_conversation_artifacts_by_message_id(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> dict[str, list[ArtifactSummary]]:
    artifacts = (
        (
            await db.execute(
                select(ConversationArtifact)
                .where(
                    ConversationArtifact.user_id == user_id,
                    ConversationArtifact.conversation_id == conversation_id,
                    ConversationArtifact.status != "deleted",
                )
                .order_by(
                    ConversationArtifact.created_at.asc(),
                    ConversationArtifact.display_name.asc(),
                )
            )
        )
        .scalars()
        .all()
    )
    if not artifacts:
        return {}

    turns_without_direct_links = {
        artifact.assistant_msg_id for artifact in artifacts if not artifact.linked_message_ids
    }
    turn_links: dict[str, list[str]] = {}
    if turns_without_direct_links:
        rows = await db.execute(
            select(MessageEvent.assistant_msg_id, MessageEvent.linked_message_ids).where(
                MessageEvent.conversation_id == conversation_id,
                MessageEvent.assistant_msg_id.in_(turns_without_direct_links),
            )
        )
        for assistant_msg_id, linked_message_ids in rows.all():
            if isinstance(linked_message_ids, list):
                turn_links[assistant_msg_id] = [
                    str(message_id) for message_id in linked_message_ids
                ]

    by_message_id: dict[str, list[ArtifactSummary]] = defaultdict(list)
    summaries = await _summaries_from_artifacts(db, artifacts)
    summary_by_artifact_id = {summary.id: summary for summary in summaries}

    for artifact in artifacts:
        linked_message_ids = artifact.linked_message_ids or turn_links.get(
            artifact.assistant_msg_id
        )
        if not linked_message_ids:
            continue
        summary = summary_by_artifact_id[artifact.id]
        for message_id in linked_message_ids:
            by_message_id[str(message_id)].append(summary)
    return by_message_id


async def list_library_artifacts(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    q: str | None,
    agent_id: uuid.UUID | None,
    conversation_id: uuid.UUID | None,
    kind: str | None,
    favorite: bool | None,
    limit: int,
    cursor: str | None,
) -> ArtifactLibraryPage:
    limit = max(1, min(limit, 100))
    filters = [
        ConversationArtifact.user_id == user_id,
        ConversationArtifact.status != "deleted",
    ]
    if q:
        pattern = f"%{q.lower()}%"
        filters.append(
            or_(
                func.lower(ConversationArtifact.display_name).like(pattern),
                func.lower(ConversationArtifact.logical_path).like(pattern),
            )
        )
    if agent_id is not None:
        filters.append(ConversationArtifact.agent_id == agent_id)
    if conversation_id is not None:
        filters.append(ConversationArtifact.conversation_id == conversation_id)
    if kind:
        filters.append(ConversationArtifact.artifact_kind == kind)
    if favorite is not None:
        filters.append(ConversationArtifact.is_favorite == favorite)
    if cursor:
        decoded_cursor = _decode_library_cursor(cursor)
        if decoded_cursor is not None:
            cursor_dt, cursor_id = decoded_cursor
            if cursor_id is not None:
                filters.append(
                    or_(
                        ConversationArtifact.created_at < cursor_dt,
                        and_(
                            ConversationArtifact.created_at == cursor_dt,
                            ConversationArtifact.id < cursor_id,
                        ),
                    )
                )
            else:
                # Backward compatibility for timestamp-only cursors issued before
                # the id tiebreaker was added.
                filters.append(ConversationArtifact.created_at < cursor_dt)

    rows = (
        (
            await db.execute(
                select(ConversationArtifact)
                .where(*filters)
                .order_by(ConversationArtifact.created_at.desc(), ConversationArtifact.id.desc())
                .limit(limit + 1)
            )
        )
        .scalars()
        .all()
    )
    has_more = len(rows) > limit
    artifacts = rows[:limit]
    items = await _summaries_from_artifacts(db, artifacts, include_names=True)
    next_cursor = _encode_library_cursor(artifacts[-1]) if has_more and artifacts else None
    return ArtifactLibraryPage(items=items, next_cursor=next_cursor, has_more=has_more)


async def list_recent_artifacts(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    limit: int,
) -> list[ArtifactSummary]:
    rows = (
        (
            await db.execute(
                select(ConversationArtifact)
                .where(
                    ConversationArtifact.user_id == user_id,
                    ConversationArtifact.status != "deleted",
                )
                .order_by(
                    ConversationArtifact.last_opened_at.desc().nullslast(),
                    ConversationArtifact.created_at.desc(),
                )
                .limit(max(1, min(limit, 50)))
            )
        )
        .scalars()
        .all()
    )
    return await _summaries_from_artifacts(db, rows, include_names=True)


async def set_artifact_favorite(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    artifact_id: uuid.UUID | str,
    is_favorite: bool,
) -> ArtifactSummary:
    artifact = await _get_owned_artifact(db, user_id=user_id, artifact_id=artifact_id)
    artifact.is_favorite = is_favorite
    await db.flush()
    return await _summary_from_artifact(db, artifact, include_names=True)


async def record_artifact_opened(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    artifact_id: uuid.UUID | str,
) -> ArtifactSummary:
    artifact = await _get_owned_artifact(db, user_id=user_id, artifact_id=artifact_id)
    artifact.last_opened_at = datetime.now(UTC).replace(tzinfo=None)
    artifact.preview_count += 1
    await db.flush()
    return await _summary_from_artifact(db, artifact, include_names=True)


async def record_artifact_download(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    artifact_id: uuid.UUID | str,
) -> ArtifactSummary:
    artifact = await _get_owned_artifact(db, user_id=user_id, artifact_id=artifact_id)
    artifact.download_count += 1
    await db.flush()
    return await _summary_from_artifact(db, artifact, include_names=True)


async def get_library_stats(db: AsyncSession, *, user_id: uuid.UUID) -> ArtifactLibraryStats:
    filters = [
        ConversationArtifact.user_id == user_id,
        ConversationArtifact.status != "deleted",
    ]
    recent_cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=7)

    total_count, total_size, favorite_count, recent_count = (
        await db.execute(
            select(
                func.count(ConversationArtifact.id),
                func.coalesce(func.sum(ConversationArtifact.size_bytes), 0),
                func.coalesce(
                    func.sum(
                        case(
                            (ConversationArtifact.is_favorite.is_(True), 1),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (ConversationArtifact.created_at >= recent_cutoff, 1),
                            else_=0,
                        )
                    ),
                    0,
                ),
            ).where(*filters)
        )
    ).one()
    kind_rows = (
        await db.execute(
            select(
                ConversationArtifact.artifact_kind,
                func.count(ConversationArtifact.id),
                func.coalesce(func.sum(ConversationArtifact.size_bytes), 0),
            )
            .where(*filters)
            .group_by(ConversationArtifact.artifact_kind)
            .order_by(ConversationArtifact.artifact_kind.asc())
        )
    ).all()

    return ArtifactLibraryStats(
        total_count=int(total_count or 0),
        total_size_bytes=int(total_size or 0),
        favorite_count=int(favorite_count or 0),
        by_kind=[
            ArtifactKindStat(kind=kind, count=int(count), size_bytes=int(size_bytes or 0))
            for kind, count, size_bytes in kind_rows
        ],
        recent_count_7d=int(recent_count or 0),
    )


async def delete_artifact(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    artifact_id: uuid.UUID | str,
    conversation_id: uuid.UUID | None = None,
) -> None:
    artifact = await _get_owned_artifact(
        db,
        user_id=user_id,
        artifact_id=artifact_id,
        conversation_id=conversation_id,
    )
    artifact.status = "deleted"
    await db.flush()


async def _get_owned_artifact(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    artifact_id: uuid.UUID | str,
    conversation_id: uuid.UUID | None = None,
) -> ConversationArtifact:
    artifact_uuid = uuid.UUID(str(artifact_id))
    filters = [
        ConversationArtifact.id == artifact_uuid,
        ConversationArtifact.user_id == user_id,
        ConversationArtifact.status != "deleted",
    ]
    if conversation_id is not None:
        filters.append(ConversationArtifact.conversation_id == conversation_id)
    artifact = (await db.execute(select(ConversationArtifact).where(*filters))).scalar_one_or_none()
    if artifact is None:
        raise ArtifactNotFoundError("artifact not found")
    return artifact


async def _get_owned_artifact_with_version(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    artifact_id: uuid.UUID | str,
    conversation_id: uuid.UUID | None = None,
) -> tuple[ConversationArtifact, ArtifactVersion]:
    artifact = await _get_owned_artifact(
        db,
        user_id=user_id,
        artifact_id=artifact_id,
        conversation_id=conversation_id,
    )
    version = await _current_version(db, artifact)
    return artifact, version
