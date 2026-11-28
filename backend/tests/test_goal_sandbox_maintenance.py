from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import goal_sandbox_cleanup_provider as provider
from app.agent_runtime.goal_sandbox_provider import goal_workspace_metadata, output_manifest
from app.authz.org_context import OrgContext
from app.config import settings
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.models.conversation_run import ConversationRun
from app.models.goal_sandbox import GoalSandbox
from app.services import goal_sandbox_leases as leases
from app.services import goal_sandbox_maintenance as maintenance
from app.services.artifact_storage import get_artifact_storage_backend
from app.services.artifacts.recorder import ArtifactDeltaRecorder, ArtifactRuntimeContext
from tests.artifact_test_helpers import seed_artifact_conversation
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession


@pytest.fixture
async def workspace(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[leases.SandboxScope, UUID]:
    conversation, agent = await seed_artifact_conversation()
    scope = leases.SandboxScope(
        TEST_TENANT_ID,
        TEST_ORG_ID,
        TEST_USER_ID,
        conversation,
        uuid4(),
        1,
        agent,
        "a" * 64,
        maintenance.goal_sandbox_provider_fingerprint(),
        "b" * 64,
        1,
        uuid4(),
    )
    run = ConversationRun(
        conversation_id=conversation,
        agent_id=agent,
        user_id=TEST_USER_ID,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        source="chat",
        status="completed",
        runtime_policy_hash=scope.policy_hash,
        runtime_policy_version=1,
    )
    db.add(run)
    await db.commit()
    monkeypatch.setattr(settings, "artifact_storage_dir", str(tmp_path / "artifacts"))
    monkeypatch.setattr(settings, "conversation_output_dir", str(tmp_path / "outputs"))
    monkeypatch.setattr(maintenance, "goal_scope_is_current", AsyncMock(return_value=True))
    return scope, run.id


async def _interrupted(
    db: AsyncSession, scope: leases.SandboxScope, run_id: UUID, manifest: dict[str, str] | None
) -> leases.SandboxClaim:
    writer = await leases.acquire(db, scope, ttl=timedelta(minutes=2))
    assert writer and await leases.bind_provider(db, writer, "private-workspace")
    if manifest is not None:
        assert await leases.record_outputs_ready(db, writer, manifest=manifest, run_id=run_id)
    assert await leases.abandon(db, writer)
    await db.commit()
    return writer


async def test_restart_recovers_and_indexes_original_output_once(
    workspace,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope, run_id = workspace
    files = {"결과.txt": "검증".encode()}
    manifest = output_manifest(files)
    writer = await _interrupted(db, scope, run_id, manifest)
    read = AsyncMock(return_value=files)
    delete = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(provider, "read_completed_outputs", read)
    monkeypatch.setattr(provider, "delete", delete)
    directory = Path(settings.conversation_output_dir) / str(scope.conversation_id)
    recorder = ArtifactDeltaRecorder(
        session_factory=TestSession,
        context=ArtifactRuntimeContext(
            scope.conversation_id,
            OrgContext(scope.org_id, scope.tenant_id, scope.user_id),
            scope.agent_id,
            str(run_id),
            directory,
        ),
        storage=get_artifact_storage_backend(),
    )
    await recorder.prepare()

    await maintenance.maintain_goal_sandboxes(maker=TestSession)
    row = await db.get(GoalSandbox, writer.workspace_id, populate_existing=True)
    assert row is not None and row.state == "retiring" and row.receipt == manifest
    assert row.output_run_id == run_id and row.deleted_at is None
    assert (directory / "결과.txt").read_bytes() == files["결과.txt"]
    restored = (await db.scalars(select(ConversationArtifact))).one()
    assert restored.assistant_msg_id == str(run_id) and restored.sha256 == manifest["결과.txt"]
    row.retry_after = None
    await db.commit()
    events = await recorder.collect_after_tool_result(
        tool_name="execute_in_skill", tool_call_id="one"
    )
    assert events and events[0]["op"] == "updated"
    assert await db.scalar(select(func.count()).select_from(ArtifactVersion)) == 1

    await maintenance.maintain_goal_sandboxes(maker=TestSession)
    clean = await db.get(GoalSandbox, writer.workspace_id, populate_existing=True)
    assert clean is not None and clean.state == "deleted"
    assert read.await_count == 1 and delete.await_count == 2


@pytest.mark.parametrize("mode", ["no_confirmation", "conflict", "corrupt", "gone", "revoked"])
async def test_uncertain_or_changed_outputs_are_never_published(
    mode: str,
    workspace,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope, run_id = workspace
    files = {"result.txt": b"stored"}
    manifest = output_manifest(files)
    writer = await _interrupted(db, scope, run_id, None if mode == "no_confirmation" else manifest)
    read = AsyncMock(return_value=None if mode == "gone" else {"result.txt": b"newer"})
    delete = AsyncMock()
    monkeypatch.setattr(provider, "read_completed_outputs", read)
    monkeypatch.setattr(provider, "delete", delete)
    if mode == "revoked":
        monkeypatch.setattr(maintenance, "goal_scope_is_current", AsyncMock(return_value=False))
    out = Path(settings.conversation_output_dir) / str(scope.conversation_id) / "result.txt"
    if mode == "conflict":
        out.parent.mkdir(parents=True)
        out.write_bytes(b"other version")
    await maintenance.maintain_goal_sandboxes(maker=TestSession)
    row = await db.get(GoalSandbox, writer.workspace_id, populate_existing=True)
    assert row is not None and row.state == "unresolved" and row.deleted_at is None
    assert (
        row.resolution_reason
        == {
            "revoked": "scope_changed",
            "no_confirmation": "unconfirmed_writer",
            "conflict": "output_conflict",
            "corrupt": "corrupt_output",
            "gone": "provider_missing",
        }[mode]
    )
    if mode == "conflict":
        assert out.read_bytes() == b"other version"
    else:
        assert not out.exists()
    assert read.await_count == int(mode in ("corrupt", "gone"))
    delete.assert_not_awaited()


async def test_recovery_recognizes_completed_local_publication_without_provider(
    workspace, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope, run_id = workspace
    files = {"already.txt": b"before shutdown"}
    manifest = output_manifest(files)
    writer = await _interrupted(db, scope, run_id, manifest)
    dst = Path(settings.conversation_output_dir) / str(scope.conversation_id) / "already.txt"
    dst.parent.mkdir(parents=True)
    dst.write_bytes(files["already.txt"])
    read = AsyncMock()
    monkeypatch.setattr(provider, "read_completed_outputs", read)
    monkeypatch.setattr(provider, "delete", AsyncMock(return_value=True))
    await maintenance.maintain_goal_sandboxes(maker=TestSession)
    row = await db.get(GoalSandbox, writer.workspace_id, populate_existing=True)
    assert row is not None and row.state == "deleted" and row.receipt == manifest
    assert await db.scalar(select(func.count()).select_from(ArtifactVersion)) == 1
    read.assert_not_awaited()


async def test_provider_checks_metadata_and_confirms_pause_or_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = uuid4()
    info = SimpleNamespace(
        id="remote",
        metadata=goal_workspace_metadata(workspace),
        status=SimpleNamespace(state="Running"),
    )
    manager = AsyncMock()

    @asynccontextmanager
    async def open_manager():
        yield manager

    monkeypatch.setattr(provider, "_manager", open_manager)
    manager.get_sandbox_info.return_value = info
    with pytest.raises(provider.WorkspaceIdentityError):
        await provider.delete(uuid4(), "remote")
    manager.kill_sandbox.assert_not_awaited()
    assert not await provider.delete(workspace, "remote")  # kill acknowledged, record still exists
    assert not await provider.pause(workspace, "remote")  # acceptance alone does not confirm pause
    info.status.state = "Paused"
    assert await provider.pause(workspace, "remote")
    assert manager.pause_sandbox.await_count == 1


def test_canonical_receipt_will_not_follow_links(tmp_path: Path) -> None:
    outside = tmp_path / "other"
    outside.write_bytes(b"private")
    folder = tmp_path / "owned"
    folder.mkdir()
    (folder / "result.txt").symlink_to(outside)
    with pytest.raises(maintenance.OutputConflict):
        maintenance._missing_outputs(folder, {"result.txt": hashlib.sha256(b"private").hexdigest()})


async def test_deleting_registered_artifact_prevents_replay_from_recreating_file(
    workspace,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.goal_sandbox_artifacts import index_goal_outputs

    scope, run_id = workspace
    name = "report.txt"
    manifest = output_manifest({name: b"report"})
    writer = await _interrupted(db, scope, run_id, manifest)
    directory = Path(settings.conversation_output_dir) / str(scope.conversation_id)
    directory.mkdir(parents=True)
    path = directory / name
    path.write_bytes(b"report")
    await index_goal_outputs(db, scope=scope, run_id=run_id, directory=directory, manifest=manifest)
    artifact = (await db.scalars(select(ConversationArtifact))).one()
    artifact.status = "deleted"
    await db.commit()
    path.unlink()
    read = AsyncMock()
    monkeypatch.setattr(provider, "read_completed_outputs", read)

    await maintenance.maintain_goal_sandboxes(maker=TestSession)

    row = await db.get(GoalSandbox, writer.workspace_id, populate_existing=True)
    assert row is not None and row.resolution_reason == "output_conflict"
    assert not path.exists()
    read.assert_not_awaited()
    assert await db.scalar(select(func.count()).select_from(ArtifactVersion)) == 1
