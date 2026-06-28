from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.models.message_event import MessageEvent
from app.schemas.artifact import (
    ArtifactKind,
    ArtifactKindStat,
    ArtifactLibraryPage,
    ArtifactLibraryStats,
    ArtifactStatus,
    ArtifactSummary,
    ArtifactTextContent,
    FileEventPayload,
)
from app.services.artifact_paths import (
    ArtifactPathError,
    NormalizedArtifactPath,
    normalize_output_path,
)
from app.services.artifact_storage import ArtifactStorageBackend, get_artifact_storage_backend

FileEventOperation = Literal["created", "updated", "deleted", "failed"]
LIBRARY_CURSOR_SEPARATOR = "|"
ARTIFACT_SOURCE_TOOL_NAMES = frozenset({"execute_in_skill", "write_file", "edit_file"})


@dataclass(frozen=True)
class ArtifactFileState:
    path: Path
    normalized: NormalizedArtifactPath
    size_bytes: int
    mtime_ns: int
    ctime_ns: int
    inode: int
    sha256: str | None


@dataclass(frozen=True)
class ArtifactSnapshot:
    files: dict[str, ArtifactFileState]


@dataclass(frozen=True)
class ArtifactDelta:
    op: FileEventOperation
    state: ArtifactFileState


@dataclass(frozen=True)
class ArtifactRuntimeContext:
    conversation_id: uuid.UUID
    user_id: uuid.UUID
    agent_id: uuid.UUID
    assistant_msg_id: str
    output_dir: Path
    source_tool_name: str = "execute_in_skill"
    branch_checkpoint_id: str | None = None
    linked_message_ids: list[str] | None = None


class ArtifactNotFoundError(LookupError):
    pass


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


class ArtifactDeltaRecorder:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        context: ArtifactRuntimeContext,
        storage: ArtifactStorageBackend | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.context = context
        self.storage = storage or get_artifact_storage_backend()
        self._snapshot = ArtifactSnapshot(files={})

    async def prepare(self) -> None:
        self._snapshot = await snapshot_output_dir(self.context.output_dir, hash_files=False)

    async def collect_after_tool_result(
        self,
        *,
        tool_name: str,
        tool_call_id: str | None,
    ) -> list[dict[str, object]]:
        if (
            tool_name not in ARTIFACT_SOURCE_TOOL_NAMES
            and tool_name != self.context.source_tool_name
        ):
            return []

        after = await snapshot_output_dir(self.context.output_dir, previous=self._snapshot)
        deltas = diff_snapshots(self._snapshot, after)
        self._snapshot = after
        if not deltas:
            return []

        async with self.session_factory() as db:
            events = await ingest_changed_files(
                db,
                context=replace(self.context, source_tool_name=tool_name),
                deltas=deltas,
                storage=self.storage,
                tool_call_id=tool_call_id,
            )
            await db.commit()
            return events


async def snapshot_output_dir(
    base_dir: Path,
    *,
    previous: ArtifactSnapshot | None = None,
    hash_files: bool = True,
) -> ArtifactSnapshot:
    base_dir = Path(base_dir)
    if not base_dir.exists():
        return ArtifactSnapshot(files={})

    files: dict[str, ArtifactFileState] = {}
    for path in sorted(base_dir.rglob("*")):
        try:
            normalized = normalize_output_path(base_dir, path)
        except ArtifactPathError:
            continue

        stat = path.stat()
        if stat.st_size > settings.artifact_max_bytes:
            continue
        previous_state = previous.files.get(normalized.logical_path) if previous else None
        if previous_state is not None and _state_matches_stat(previous_state, stat):
            sha256 = previous_state.sha256
        elif hash_files:
            sha256 = await _sha256_file(path)
        else:
            sha256 = None

        files[normalized.logical_path] = ArtifactFileState(
            path=path,
            normalized=normalized,
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            ctime_ns=stat.st_ctime_ns,
            inode=int(getattr(stat, "st_ino", 0)),
            sha256=sha256,
        )

    return ArtifactSnapshot(files=files)


def _state_matches_stat(state: ArtifactFileState, stat: os.stat_result) -> bool:
    return (
        state.size_bytes == stat.st_size
        and state.mtime_ns == stat.st_mtime_ns
        and state.ctime_ns == stat.st_ctime_ns
        and state.inode == int(getattr(stat, "st_ino", 0))
    )


def _states_have_same_metadata(left: ArtifactFileState, right: ArtifactFileState) -> bool:
    return (
        left.size_bytes == right.size_bytes
        and left.mtime_ns == right.mtime_ns
        and left.ctime_ns == right.ctime_ns
        and left.inode == right.inode
    )


def diff_snapshots(before: ArtifactSnapshot, after: ArtifactSnapshot) -> list[ArtifactDelta]:
    deltas: list[ArtifactDelta] = []
    for logical_path, after_state in after.files.items():
        before_state = before.files.get(logical_path)
        if before_state is None:
            deltas.append(ArtifactDelta(op="created", state=after_state))
            continue
        if not _states_have_same_metadata(before_state, after_state) or (
            before_state.sha256 is not None
            and after_state.sha256 is not None
            and before_state.sha256 != after_state.sha256
        ):
            deltas.append(ArtifactDelta(op="updated", state=after_state))
    for logical_path, before_state in before.files.items():
        if logical_path not in after.files:
            deltas.append(ArtifactDelta(op="deleted", state=before_state))
    return deltas[: settings.artifact_max_files_per_run]


async def ingest_changed_files(
    db: AsyncSession,
    *,
    context: ArtifactRuntimeContext,
    deltas: list[ArtifactDelta],
    storage: ArtifactStorageBackend,
    tool_call_id: str | None,
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for delta in deltas:
        state = delta.state
        normalized = state.normalized
        if delta.op == "deleted":
            deleted_artifacts = (
                (
                    await db.execute(
                        select(ConversationArtifact)
                        .where(
                            ConversationArtifact.conversation_id == context.conversation_id,
                            ConversationArtifact.user_id == context.user_id,
                            ConversationArtifact.assistant_msg_id == context.assistant_msg_id,
                            ConversationArtifact.logical_path == normalized.logical_path,
                            ConversationArtifact.status != "deleted",
                        )
                        .order_by(ConversationArtifact.updated_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            for artifact in deleted_artifacts:
                artifact.tool_call_id = tool_call_id
                artifact.source_tool_name = context.source_tool_name
                artifact.status = "deleted"
                artifact.branch_checkpoint_id = context.branch_checkpoint_id
                artifact.linked_message_ids = (
                    context.linked_message_ids or artifact.linked_message_ids
                )
                await db.flush()
                summary = await _summary_from_artifact(db, artifact)
                events.append(_file_event_payload("deleted", summary).model_dump(mode="json"))
            continue

        sha256 = state.sha256 or await _sha256_file(state.path)
        existing = (
            await db.execute(
                select(ConversationArtifact).where(
                    and_(
                        ConversationArtifact.conversation_id == context.conversation_id,
                        ConversationArtifact.assistant_msg_id == context.assistant_msg_id,
                        ConversationArtifact.logical_path == normalized.logical_path,
                    )
                )
            )
        ).scalar_one_or_none()

        op: FileEventOperation = "created" if existing is None else "updated"
        version_number = 1
        if existing is not None:
            version_number = (
                (
                    await db.execute(
                        select(func.max(ArtifactVersion.version_number)).where(
                            ArtifactVersion.artifact_id == existing.id
                        )
                    )
                ).scalar_one()
                or 0
            ) + 1

        artifact = existing or ConversationArtifact(
            id=uuid.uuid4(),
            user_id=context.user_id,
            agent_id=context.agent_id,
            conversation_id=context.conversation_id,
            assistant_msg_id=context.assistant_msg_id,
            logical_path=normalized.logical_path,
            display_name=normalized.display_name,
            extension=normalized.extension,
            mime_type=normalized.mime_type,
            artifact_kind=normalized.artifact_kind,
            size_bytes=state.size_bytes,
            sha256=sha256,
            status="ready",
            source_tool_name=context.source_tool_name,
            tool_call_id=tool_call_id,
            branch_checkpoint_id=context.branch_checkpoint_id,
            linked_message_ids=context.linked_message_ids,
            metadata_json={},
        )
        if existing is None:
            db.add(artifact)
            await db.flush()

        stored = await storage.put_file(
            conversation_id=context.conversation_id,
            artifact_id=artifact.id,
            version_number=version_number,
            display_name=normalized.display_name,
            source_path=state.path,
        )
        version = ArtifactVersion(
            id=uuid.uuid4(),
            artifact_id=artifact.id,
            version_number=version_number,
            storage_provider=stored.storage_provider,
            bucket=stored.bucket,
            object_key=stored.object_key,
            original_filename=normalized.display_name,
            size_bytes=state.size_bytes,
            sha256=sha256,
            metadata_json={},
        )
        db.add(version)

        artifact.tool_call_id = tool_call_id
        artifact.source_tool_name = context.source_tool_name
        artifact.display_name = normalized.display_name
        artifact.extension = normalized.extension
        artifact.mime_type = normalized.mime_type
        artifact.artifact_kind = normalized.artifact_kind
        artifact.size_bytes = state.size_bytes
        artifact.sha256 = sha256
        artifact.status = "ready"
        artifact.current_version_id = version.id
        artifact.branch_checkpoint_id = context.branch_checkpoint_id
        artifact.linked_message_ids = context.linked_message_ids

        await db.flush()
        summary = await _summary_from_artifact(db, artifact)
        events.append(_file_event_payload(op, summary).model_dump(mode="json"))

    return events


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


async def link_artifacts_to_messages(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    assistant_msg_id: str,
    linked_message_ids: list[str],
) -> None:
    if not linked_message_ids:
        return

    await db.execute(
        update(ConversationArtifact)
        .where(
            ConversationArtifact.conversation_id == conversation_id,
            ConversationArtifact.assistant_msg_id == assistant_msg_id,
            ConversationArtifact.status != "deleted",
        )
        .values(linked_message_ids=linked_message_ids)
    )


async def finalize_artifacts_for_run(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    assistant_msg_id: str,
    run_status: str,
) -> int:
    """Close artifact state for a terminal run.

    Artifacts are discovered after individual tool results and are therefore
    optimistically marked ``ready`` while the assistant turn is still running.
    If the run later ends as canceled/stale/failed, those optimistic ready
    rows should not render as healthy completed outputs.
    """

    if run_status not in {"canceled", "stale", "failed"}:
        return 0

    result = await db.execute(
        update(ConversationArtifact)
        .where(
            ConversationArtifact.conversation_id == conversation_id,
            ConversationArtifact.assistant_msg_id == assistant_msg_id,
            ConversationArtifact.status.in_(("writing", "ready")),
        )
        .values(status="failed")
    )
    return int(result.rowcount or 0)


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


async def read_artifact_text_content(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    artifact_id: uuid.UUID | str,
    conversation_id: uuid.UUID | None = None,
    storage: ArtifactStorageBackend | None = None,
    max_bytes: int | None = None,
) -> ArtifactTextContent:
    artifact, version = await _get_owned_artifact_with_version(
        db,
        user_id=user_id,
        artifact_id=artifact_id,
        conversation_id=conversation_id,
    )
    storage = storage or get_artifact_storage_backend()
    path = await _storage_local_path(storage, object_key=version.object_key)
    max_bytes = max_bytes or settings.artifact_preview_max_text_bytes
    try:
        data = await asyncio.to_thread(_read_file_prefix, path, max_bytes + 1)
    except FileNotFoundError as exc:
        raise ArtifactNotFoundError("artifact object not found") from exc
    truncated = len(data) > max_bytes
    text = data[:max_bytes].decode("utf-8", errors="replace")
    return ArtifactTextContent(
        text=text,
        truncated=truncated,
        mime_type=artifact.mime_type,
        size_bytes=artifact.size_bytes,
    )


async def get_artifact_download_path(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    artifact_id: uuid.UUID | str,
    conversation_id: uuid.UUID | None = None,
    storage: ArtifactStorageBackend | None = None,
) -> tuple[ConversationArtifact, ArtifactVersion, Path]:
    artifact, version = await _get_owned_artifact_with_version(
        db,
        user_id=user_id,
        artifact_id=artifact_id,
        conversation_id=conversation_id,
    )
    storage = storage or get_artifact_storage_backend()
    path = await _storage_local_path(storage, object_key=version.object_key)
    return artifact, version, path


async def get_conversation_artifact_summary(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    artifact_id: uuid.UUID | str,
) -> ArtifactSummary:
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
    artifact = await _get_owned_artifact(db, user_id=user_id, artifact_id=artifact_id)
    return await _summary_from_artifact(db, artifact, include_names=True)


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


def _read_file_prefix(path: Path, byte_count: int) -> bytes:
    with path.open("rb") as file_obj:
        return file_obj.read(byte_count)


async def _storage_local_path(storage: ArtifactStorageBackend, *, object_key: str) -> Path:
    try:
        return await storage.local_path(object_key=object_key)
    except (FileNotFoundError, ValueError) as exc:
        raise ArtifactNotFoundError("artifact object not found") from exc


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
            agent_names = {agent_id: name for agent_id, name in agent_rows.all()}
        if conversation_ids:
            conversation_rows = await db.execute(
                select(Conversation.id, Conversation.title).where(
                    Conversation.id.in_(conversation_ids)
                )
            )
            conversation_titles = {
                conversation_id: title for conversation_id, title in conversation_rows.all()
            }

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


async def _sha256_file(path: Path) -> str:
    return await asyncio.to_thread(_sha256_file_sync, path)


def _sha256_file_sync(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_event_payload(op: FileEventOperation, summary: ArtifactSummary) -> FileEventPayload:
    return FileEventPayload(op=op, **summary.model_dump())


def is_text_preview_artifact(summary: ArtifactSummary) -> bool:
    if summary.mime_type.startswith("text/"):
        return True
    if summary.artifact_kind in {"markdown", "code", "html"}:
        return True
    return summary.extension in {"csv", "tsv", "json", "txt", "log", "yaml", "yml", "toml"}
