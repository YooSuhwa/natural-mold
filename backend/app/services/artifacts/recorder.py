"""Artifact delta recording (BE-S8 split).

Streaming hot path: output-dir snapshots, delta diffing, changed-file ingest
into ``conversation_artifacts``/``artifact_versions``, and per-run finalize/
link maintenance. Called by the conversation stream service after each tool
result.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.artifact_scope import validated_artifact_conversation
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.error_codes import conversation_not_found
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.services.artifact_storage import ArtifactStorageBackend, get_artifact_storage_backend
from app.services.artifacts.run_links import (
    finalize_artifacts_for_run as finalize_artifacts_for_run,
)
from app.services.artifacts.run_links import (
    link_artifacts_to_messages as link_artifacts_to_messages,
)
from app.services.artifacts.snapshots import (
    ArtifactDelta as ArtifactDelta,
)
from app.services.artifacts.snapshots import (
    ArtifactFileState as ArtifactFileState,
)
from app.services.artifacts.snapshots import (
    ArtifactSnapshot as ArtifactSnapshot,
)
from app.services.artifacts.snapshots import (
    _state_matches_stat as _state_matches_stat,
)
from app.services.artifacts.snapshots import (
    _states_have_same_metadata as _states_have_same_metadata,
)
from app.services.artifacts.snapshots import (
    diff_snapshots as diff_snapshots,
)
from app.services.artifacts.snapshots import (
    snapshot_output_dir as snapshot_output_dir,
)
from app.services.artifacts.summary import (
    FileEventOperation,
    _file_event_payload,
    _summary_from_artifact,
)

ARTIFACT_SOURCE_TOOL_NAMES = frozenset({"execute_in_skill", "write_file", "edit_file"})


@dataclass(frozen=True)
class ArtifactRuntimeContext:
    conversation_id: uuid.UUID
    org_context: OrgContext
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
        session_factory: Callable[[], AsyncSession],
        context: ArtifactRuntimeContext,
        storage: ArtifactStorageBackend | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.context = context
        self.storage = storage or get_artifact_storage_backend()
        self._snapshot = ArtifactSnapshot(files={})

    async def prepare(self) -> None:
        async with self.session_factory() as db:
            await validated_artifact_conversation(
                db, self.context.conversation_id, self.context.org_context
            )
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

        async with self.session_factory() as db:
            await validated_artifact_conversation(
                db, self.context.conversation_id, self.context.org_context
            )
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

    conversation = await validated_artifact_conversation(
        db, context.conversation_id, context.org_context
    )
    if conversation.agent_id != context.agent_id:
        raise conversation_not_found()
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
                            private_scope_predicate(ConversationArtifact, context.org_context),
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
                        private_scope_predicate(ConversationArtifact, context.org_context),
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
            user_id=context.org_context.user_id,
            org_id=context.org_context.org_id,
            tenant_id=context.org_context.tenant_id,
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
            tenant_id=context.org_context.tenant_id,
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
