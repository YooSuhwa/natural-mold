"""Artifact delta recording (BE-S8 split).

Streaming hot path: output-dir snapshots, delta diffing, changed-file ingest
into ``conversation_artifacts``/``artifact_versions``, and per-run finalize/
link maintenance. Called by the conversation stream service after each tool
result.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.services.artifact_paths import (
    ArtifactPathError,
    NormalizedArtifactPath,
    normalize_output_path,
)
from app.services.artifact_storage import ArtifactStorageBackend, get_artifact_storage_backend
from app.services.artifacts.summary import (
    FileEventOperation,
    _file_event_payload,
    _summary_from_artifact,
)

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
    # Call-time facade lookup: tests monkeypatch artifact_service._sha256_file
    # to observe hashing; a top-level import of the concrete module would
    # early-bind and silently bypass that patch.
    from app.services.artifact_service import _sha256_file

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
    # Call-time facade lookup: see snapshot_output_dir.
    from app.services.artifact_service import _sha256_file

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
