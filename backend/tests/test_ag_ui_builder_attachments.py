"""Skill Builder files are materialized by the committed AG-UI worker."""

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import pytest
from ag_ui.core import RunAgentInput
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_config import AgentConfig
from app.authz import runtime
from app.authz.conversation_run_scope import RunExecutionTarget
from app.config import settings
from app.models.ag_ui_command_receipt import AgUiCommandReceipt
from app.models.message_attachment import MessageAttachment
from app.services import conversation_run_execution as execution
from app.services import conversation_run_queue_service as queue
from app.services import conversation_run_queue_worker as queue_worker
from app.services import conversation_run_worker as worker
from tests.authz.f05_support import enforce
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID
from tests.skill_builder_test_helpers import configure_system_llm
from tests.test_ag_ui_start import _submit


@pytest.mark.parametrize(
    "outcome", ["rollback", "restart", "detached", "wrong-workspace", "revoked-target"]
)
async def test_committed_worker_owns_builder_attachments(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    outcome: Literal["rollback", "restart", "detached", "wrong-workspace", "revoked-target"],
) -> None:
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    await configure_system_llm(db)
    builder = await client.post(
        "/api/skill-builder", json={"mode": "create", "user_request": "Fixture skill"}
    )
    assert builder.status_code == 201, builder.text
    conversation_id = UUID(builder.json()["conversation_id"])
    draft_dir = tmp_path / "skill-drafts" / builder.json()["id"] / "inputs"
    upload = await client.post(
        "/api/uploads", files={"file": ("sample.txt", b"input", "text/plain")}
    )
    assert upload.status_code == 201, upload.text
    upload_id = UUID(upload.json()["id"])
    wire = RunAgentInput.model_validate(
        {
            "runId": "builder-accept",
            "threadId": str(conversation_id),
            "messages": [{"id": "question", "role": "user", "content": "Read the sample"}],
            "forwardedProps": {"moldy": {"attachmentIds": [str(upload_id)]}},
        }
    )
    bound = await _submit(db, wire)
    assert bound.input_id is not None
    assert not list(draft_dir.glob("*"))
    if outcome == "rollback":
        await db.rollback()
        remaining = await db.get(MessageAttachment, upload_id, populate_existing=True)
        assert remaining is not None and remaining.conversation_id is None
        assert await db.get(AgUiCommandReceipt, (conversation_id, wire.run_id)) is None
        assert not list(draft_dir.glob("*"))
        return
    await db.commit()
    claimed = await queue.claim_next_input(
        db, conversation_id=conversation_id, context=unit_context(TEST_USER_ID)
    )
    assert claimed is not None and claimed.input.id == bound.input_id
    await db.commit()
    assert not list(draft_dir.glob("*"))
    if outcome == "detached":
        upload_row = await db.get(MessageAttachment, upload_id)
        assert upload_row is not None
        upload_row.conversation_id = None
        await db.commit()
    if outcome == "wrong-workspace":
        resolve = queue_worker.resolve_agent_context

        async def wrong_workspace(*args: Any, **kwargs: Any) -> AgentConfig:
            cfg = await resolve(*args, **kwargs)
            cfg.draft_workspace_path = "skill-drafts/other"
            return cfg

        monkeypatch.setattr(queue_worker, "resolve_agent_context", wrong_workspace)

    checked: list[UUID] = []
    if outcome == "revoked-target":
        materialize = execution.materialize_builder_attachments

        async def revoke_after_worker_claim(
            session: AsyncSession, *, target: RunExecutionTarget, **kwargs: Any
        ) -> None:
            agent_id = target.conversation.agent_id
            # The agent remains runnable, but the separate create-skill capability is gone.
            monkeypatch.setattr(
                runtime,
                "_current",
                enforce((f"user:{TEST_USER_ID}", "can_run", f"agent:{agent_id}")),
            )
            checked.append(target.run_id)
            await materialize(session, target=target, **kwargs)

        monkeypatch.setattr(execution, "materialize_builder_attachments", revoke_after_worker_claim)

    calls: list[bytes] = []

    async def execute(_cfg: AgentConfig, _input: Any, **_kwargs: Any) -> AsyncGenerator[str, None]:
        calls.extend(path.read_bytes() for path in draft_dir.glob("*"))
        if False:
            yield ""

    monkeypatch.setattr(queue_worker, "execute_agent_stream_langgraph", execute)
    registry = worker.RunTaskRegistry(worker_instance_id="restarted-builder")
    worker.reset_run_task_registry_for_tests(registry)
    assert await queue_worker.recover_conversation_queue() == 1
    task = registry.get(claimed.run.id)
    if task is not None:
        await task
    else:
        # Detached validation can reject the saved upload before spawning the worker.
        assert outcome == "detached"
    await db.refresh(claimed.run)
    if outcome == "restart":
        assert claimed.run.status == "completed" and calls == [b"input"]
        assert await queue_worker.recover_conversation_queue() == 0
        assert [path.read_bytes() for path in draft_dir.glob("*")] == [b"input"]
    else:
        assert claimed.run.status == "failed" and not calls
        if outcome == "revoked-target":
            assert checked == [claimed.run.id]
        assert not list(draft_dir.glob("*"))
        assert not (tmp_path / "skill-drafts" / "other").exists()
