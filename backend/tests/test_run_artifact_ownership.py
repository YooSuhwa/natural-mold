"""Only the current chat run generation can publish an artifact version."""

from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.conversation_artifact import ArtifactVersion
from app.models.conversation_run import ConversationRun
from app.services.artifact_service import ArtifactDeltaRecorder, ArtifactRuntimeContext
from app.services.artifact_storage import LocalArtifactStorageBackend
from app.services.conversation_run_fence import RunOwnershipLost, claim_run_owner, run_fence_scope
from tests.artifact_test_helpers import seed_artifact_conversation
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, TestSession


@pytest.mark.asyncio
async def test_replaced_worker_cannot_publish_or_replace_artifact(tmp_path: Path) -> None:
    conversation_id, agent_id = await seed_artifact_conversation()
    org = unit_context(TEST_USER_ID)
    async with TestSession() as db:
        run = ConversationRun(
            conversation_id=conversation_id,
            agent_id=agent_id,
            user_id=org.user_id,
            tenant_id=org.tenant_id,
            org_id=org.org_id,
            source="chat",
            status="running",
            is_active=True,
        )
        db.add(run)
        await db.flush()
        first = claim_run_owner(run, "worker")
        await db.commit()
        run_id = run.id

    storage = LocalArtifactStorageBackend(tmp_path / "storage")
    output = tmp_path / "output"
    output.mkdir()
    context = ArtifactRuntimeContext(conversation_id, org, agent_id, str(run_id), output)
    with run_fence_scope(first):
        previous = ArtifactDeltaRecorder(
            session_factory=TestSession, storage=storage, context=context
        )
        await previous.prepare()
    async with TestSession() as db:
        run = await db.get(ConversationRun, run_id, with_for_update=True)
        assert run is not None
        # Same process label after restart, different durable generation and token.
        second = claim_run_owner(run, "worker")
        await db.commit()

    with run_fence_scope(second):
        current = ArtifactDeltaRecorder(
            session_factory=TestSession, storage=storage, context=context
        )
        await current.prepare()
        path = output / "report.md"
        path.write_text("approved result", encoding="utf-8")
        events = await current.collect_after_tool_result(
            tool_name="write_file", tool_call_id="call"
        )
        assert len(events) == 1
        path.write_text("stale write", encoding="utf-8")
        # A delayed callback retains the capability of the recorder that created it.
        with pytest.raises(RunOwnershipLost):
            await previous.collect_after_tool_result(tool_name="write_file", tool_call_id="call")

    async with TestSession() as db:
        version = (await db.scalars(select(ArtifactVersion))).one()
        blob = await storage.local_path(object_key=version.object_key, tenant_id=org.tenant_id)
        assert blob.read_text(encoding="utf-8") == "approved result"
        assert [p for p in (tmp_path / "storage").rglob("*") if p.is_file()] == [blob]
