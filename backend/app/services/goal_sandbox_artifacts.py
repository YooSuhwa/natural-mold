"""Index verified goal output receipts in the original run's artifact library."""

from __future__ import annotations

import asyncio
import hashlib
import os
import stat
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.goal_sandbox_provider import valid_output_name
from app.agent_runtime.skill_sandbox import (
    MAX_OUTPUT_FILE_BYTES,
    MAX_OUTPUT_TOTAL_BYTES,
    SkillSandboxError,
)
from app.authz.org_context import OrgContext
from app.config import settings
from app.models.conversation_artifact import ConversationArtifact
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.services.artifact_paths import normalize_output_path
from app.services.artifact_storage import get_artifact_storage_backend
from app.services.artifacts.recorder import ArtifactRuntimeContext, ingest_changed_files
from app.services.artifacts.run_links import finalize_artifacts_for_run
from app.services.artifacts.snapshots import ArtifactDelta, ArtifactFileState
from app.services.cancel_deferred import finish_then_cancel

if TYPE_CHECKING:
    from app.services.goal_sandbox_leases import SandboxScope


def _stage(directory: Path, snapshot: Path, manifest: dict[str, str]) -> list[ArtifactDelta]:
    """Hash the same bytes that storage will consume, with fixed limits on concurrent writers."""
    deltas = []
    total = 0
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name, expected in manifest.items():
            if not valid_output_name(name):
                raise SkillSandboxError("goal output path is invalid.")
            source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            destination = snapshot / name
            with os.fdopen(source, "rb") as stream:
                info = os.fstat(stream.fileno())
                total += info.st_size
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_size > min(MAX_OUTPUT_FILE_BYTES, settings.artifact_max_bytes)
                    or total > MAX_OUTPUT_TOTAL_BYTES
                ):
                    raise SkillSandboxError("goal output size or file type is invalid.")
                remaining = info.st_size
                digest = hashlib.sha256()
                with destination.open("xb") as copy:
                    while remaining:
                        block = stream.read(min(remaining, 65536))
                        if not block:
                            raise SkillSandboxError("goal output changed before artifact indexing.")
                        copy.write(block)
                        digest.update(block)
                        remaining -= len(block)
                if stream.read(1) or digest.hexdigest() != expected:
                    raise SkillSandboxError("goal output changed before artifact indexing.")
            normalized = normalize_output_path(snapshot, destination)
            state = ArtifactFileState(
                destination,
                normalized,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
                info.st_ino,
                expected,
            )
            deltas.append(ArtifactDelta("created", state))
        return deltas
    finally:
        os.close(fd)


async def check_goal_output_destinations(
    db: AsyncSession, *, scope: SandboxScope, run_id: UUID, manifest: dict[str, str]
) -> None:
    """Lock any prior version before publishing; respect a user's delete or edit."""
    if not manifest:
        return
    artifacts = await db.scalars(
        select(ConversationArtifact)
        .where(
            ConversationArtifact.conversation_id == scope.conversation_id,
            ConversationArtifact.tenant_id == scope.tenant_id,
            ConversationArtifact.org_id == scope.org_id,
            ConversationArtifact.user_id == scope.user_id,
            ConversationArtifact.assistant_msg_id == str(run_id),
            ConversationArtifact.logical_path.in_(list(manifest)),
        )
        .with_for_update()
    )
    for artifact in artifacts:
        if artifact.status == "deleted" or artifact.sha256 != manifest[artifact.logical_path]:
            raise SkillSandboxError("goal artifact conflicts with an existing version.")


async def index_goal_outputs(
    db: AsyncSession,
    *,
    scope: SandboxScope,
    run_id: UUID,
    directory: Path,
    manifest: dict[str, str],
) -> None:
    if not manifest:
        return
    if len(manifest) > 1024:
        raise SkillSandboxError("goal workspace has too many outputs.")
    run = await db.scalar(
        select(ConversationRun).where(
            ConversationRun.id == run_id,
            ConversationRun.conversation_id == scope.conversation_id,
            ConversationRun.user_id == scope.user_id,
            ConversationRun.tenant_id == scope.tenant_id,
            ConversationRun.org_id == scope.org_id,
            ConversationRun.agent_id == scope.agent_id,
            ConversationRun.runtime_policy_hash == scope.policy_hash,
            ConversationRun.runtime_policy_version == scope.policy_version,
        )
    )
    if run is None:
        raise SkillSandboxError("goal output run attribution is unavailable.")
    actor = OrgContext(scope.org_id, scope.tenant_id, scope.user_id)
    checkpoint_id = await db.scalar(
        select(ConversationRunInput.checkpoint_id).where(
            ConversationRunInput.run_id == run_id,
            ConversationRunInput.conversation_id == scope.conversation_id,
        )
    )
    context = ArtifactRuntimeContext(
        scope.conversation_id,
        actor,
        scope.agent_id,
        str(run_id),
        directory,
        branch_checkpoint_id=checkpoint_id,
    )
    with tempfile.TemporaryDirectory(prefix="moldy-goal-artifact-") as tmp:
        deltas = await finish_then_cancel(asyncio.to_thread(_stage, directory, Path(tmp), manifest))
        await ingest_changed_files(
            db,
            context=context,
            deltas=deltas,
            storage=get_artifact_storage_backend(),
            tool_call_id=None,
            goal_output_receipt=True,
        )
    await finalize_artifacts_for_run(
        db,
        conversation_id=scope.conversation_id,
        context=actor,
        assistant_msg_id=str(run_id),
        run_status=run.status,
    )
