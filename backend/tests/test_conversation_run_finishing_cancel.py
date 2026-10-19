"""A local stop that lands after the run body ended must not tear the owner's finalization.

Post-commit stops (organization archive ``_stop_local_runs``, the heartbeat seeing an
inactive row) call ``request_cancel`` on the owner task. Once the body has ended the
owner's own finalization reconciles the durable row under its lock, so a cancel
delivered there only interrupted the terminal transition, the broker close and the
queue dispatch, and the task ended ``CancelledError`` (PR #314 CI, task10 rollback gap).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

import anyio
import pytest

from app.agent_runtime.run_metrics import RunMetricsAccumulator
from app.authz.conversation_scope import require_conversation_agent_id
from app.dependencies import CurrentUser
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.services import conversation_run_execution, conversation_run_service
from app.services import conversation_run_worker as worker
from tests.authz.run_fixture_config import run_fixture_config
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, TestSession
from tests.integration import _seed as seed_helpers


async def _queued_run() -> tuple[Any, Any]:
    conversation_id = await seed_helpers.seed_conversation_with_agent()
    async with TestSession() as db:
        conversation = await db.get(Conversation, conversation_id)
        assert conversation is not None
        run = await conversation_run_service.create_run(
            db,
            conversation_id=conversation_id,
            agent_id=require_conversation_agent_id(conversation),
            context=unit_context(TEST_USER_ID),
            source="chat",
            input_preview="fails, then a stop arrives while finishing",
        )
        await db.commit()
        return conversation_id, run.id


@pytest.mark.parametrize("phase", ["error_handler", "finalization"])
async def test_stop_after_the_body_ended_never_cancels_the_owner(
    monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    conversation_id, run_id = await _queued_run()
    registry = worker.RunTaskRegistry(worker_instance_id="finishing-owner")
    stop_results: list[bool] = []

    async def failing_executor(*_args: Any, **_kwargs: Any) -> AsyncGenerator[str, None]:
        raise RuntimeError("model call failed")
        yield ""  # pragma: no cover - keeps this an async generator

    async def idle_heartbeat(*_args: Any) -> None:
        await asyncio.Event().wait()

    async def prepared_metrics(**kwargs: Any) -> RunMetricsAccumulator:
        return RunMetricsAccumulator(started_at=kwargs["started_at"])

    def stop_then_yield(original: Any) -> Any:
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            # An archive commit's local stop, delivered at the next suspension point.
            stop_results.append(registry.request_cancel(run_id, reason="stop"))
            await asyncio.sleep(0)
            return await original(*args, **kwargs)

        return wrapped

    monkeypatch.setattr(worker, "_heartbeat_until_terminal", idle_heartbeat)
    monkeypatch.setattr(worker, "_prepare_run_metrics", prepared_metrics)
    if phase == "error_handler":
        original = conversation_run_execution._publish_error
        monkeypatch.setattr(conversation_run_execution, "_publish_error", stop_then_yield(original))
    else:
        monkeypatch.setattr(worker, "_transition", stop_then_yield(worker._transition))

    ctx = await worker.start_conversation_run(
        run_id=run_id,
        conversation_id=conversation_id,
        cfg=await run_fixture_config(run_id, TestSession),
        user=CurrentUser(id=TEST_USER_ID, email="owner@test", name="Owner"),
        input_payload={"messages": [{"role": "user", "content": "go"}]},
        moldy_source="chat",
        executor_fn=failing_executor,
        registry=registry,
    )
    owner = registry.get(run_id)
    assert owner is not None
    with anyio.fail_after(5):
        await asyncio.wait({owner})

    assert not owner.cancelled()
    assert owner.exception() is None
    assert stop_results == [False]
    assert ctx.broker.is_closed
    assert registry.get(run_id) is None
    async with TestSession() as db:
        row = await db.get(ConversationRun, run_id)
    assert row is not None
    assert (row.status, row.is_active, row.error_code) == ("failed", False, "runtime_error")
