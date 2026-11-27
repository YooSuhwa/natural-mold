from __future__ import annotations

import uuid
from dataclasses import replace
from time import monotonic

import pytest

from app.agent_runtime.langgraph_agent_stream_runner import execute_agent_stream_langgraph
from app.agent_runtime.run_metrics import RunMetricsAccumulator
from app.authz.conversation_scope import require_conversation_agent_id
from app.config import settings
from app.dependencies import CurrentUser
from app.models.conversation import Conversation
from app.services import conversation_run_service, conversation_run_worker
from app.services.conversation_run_fence import (
    RunOwnershipLost,
    claim_run_owner,
    fence_for_run,
    lock_run_fence,
    run_fence_scope,
)
from app.services.conversation_run_owned_start import prepare_owned_start
from app.services.conversation_run_transitions import _persist_shutdown_intent, _transition
from tests.authz.run_fixture_config import run_fixture_config
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, TestSession
from tests.integration._seed import seed_conversation_with_agent


@pytest.mark.asyncio
async def test_direct_text_claim_and_replacement_fence_old_shutdown_and_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "chat_run_recovery_enabled", True)
    monkeypatch.setattr(settings, "prepared_run_lease_enabled", False)
    monkeypatch.setattr(conversation_run_worker, "async_session", TestSession)
    conversation_id = await seed_conversation_with_agent()
    async with TestSession() as db:
        conversation = await db.get(Conversation, conversation_id)
        assert conversation is not None
        run = await conversation_run_service.create_run(
            db,
            conversation_id=conversation_id,
            agent_id=require_conversation_agent_id(conversation),
            context=unit_context(TEST_USER_ID),
            source="chat",
            input_preview="hello",
        )
        await db.commit()
        run_id = run.id
    cfg = replace(
        await run_fixture_config(run_id, TestSession),
        api_key="fixture-key",
        model_id=str(uuid.uuid4()),
        resolved_model_credential_id=str(uuid.uuid4()),
    )
    user = CurrentUser(id=TEST_USER_ID, email="test@example.com", name="Test", is_super_user=True)
    payload = {
        "messages": [
            {"type": "human", "content": "hello", "additional_kwargs": {}, "response_metadata": {}}
        ]
    }
    start, pinned, _ = await prepare_owned_start(
        run_id=run_id,
        conversation_id=conversation_id,
        cfg=cfg,
        user=user,
        input_payload=payload,
        moldy_source="chat",
        executor_fn=execute_agent_stream_langgraph,
        attachment_ids=None,
        config_transform=None,
        worker_instance_id="same-instance-name",
        started_at=monotonic(),
        recovery_fence=None,
    )
    assert start.started and start.run and start.run.recovery_runtime_digest
    first = fence_for_run(start.run)
    assert first.generation == 1
    async with TestSession() as db:
        row = await lock_run_fence(db, first)
        second = claim_run_owner(row, first.worker_instance_id)
        await db.commit()
    assert second.generation == 2 and second.owner_token != first.owner_token
    with run_fence_scope(first), pytest.raises(RunOwnershipLost):
        await _transition(
            run_id,
            "completed",
            tenant=first.tenant,
            model_name=cfg.model_name,
            run_metrics=RunMetricsAccumulator(started_at=monotonic()),
        )
    await _persist_shutdown_intent([first])
    async with TestSession() as db:
        current = await lock_run_fence(db, second)
        assert current.status == "running" and current.cancel_requested_at is None

    adopted, _, payload = await prepare_owned_start(
        run_id=run_id,
        conversation_id=conversation_id,
        cfg=pinned,
        user=user,
        input_payload=None,
        moldy_source="recovery",
        executor_fn=execute_agent_stream_langgraph,
        attachment_ids=None,
        config_transform=None,
        worker_instance_id=second.worker_instance_id,
        started_at=monotonic(),
        recovery_fence=second,
    )
    assert (
        adopted.started and adopted.run and fence_for_run(adopted.run) == second and payload is None
    )
    await _persist_shutdown_intent([second])
    async with TestSession() as db:
        assert (await lock_run_fence(db, second, allow_canceling=True)).status == "canceling"
