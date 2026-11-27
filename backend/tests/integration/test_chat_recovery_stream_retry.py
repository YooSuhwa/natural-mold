"""A retriable gateway frame arriving during worker handoff cannot cause another send."""

from __future__ import annotations

import asyncio
import multiprocessing
from functools import partial
from uuid import UUID

import pytest
from langchain.agents import create_agent

from app.agent_runtime.runtime.direct_stream_retry import DirectStreamRetryMiddleware
from app.agent_runtime.runtime_component_builder import _build_model_candidates
from app.agent_runtime.stream_retry_authority import check_stream_attempt
from app.authz.conversation_run_scope import RunConversationTarget, RunExecutionTarget
from app.database import tenant_session
from app.models.conversation_run import ConversationRun
from app.services import conversation_run_service, conversation_stream_service, trace_storage
from app.services.conversation_run_delivery import _publish_error
from app.services.conversation_run_fence import (
    RunOwnershipLost,
    claim_run_owner,
    fence_for_run,
    run_fence_scope,
)
from app.services.conversation_run_task_registry import RunTaskRegistry
from app.services.stream_attempt_owner import stream_attempt_owner
from tests.agent_runtime.test_direct_stream_retry import (
    CONCURRENCY,
    Gateway,
    _chunk,
    _config,
    _error,
    _messages,
    _stream,
)
from tests.agent_runtime.test_direct_stream_retry import gateway as gateway
from tests.authz.unit_scope_fixture import unit_context
from tests.integration.test_conversation_queue_concurrency import (
    _postgres_session_factory,
    _seed_conversation,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.filterwarnings("ignore:The v3 streaming protocol on Pregel is experimental"),
]


def _replacement_process(run_id: UUID, worker: str) -> None:
    """Claim independently, without inheriting the first worker's registry or connection."""

    async def transfer() -> None:
        sessions, engine = _postgres_session_factory()
        try:
            async with sessions() as db:
                locked = await db.get(ConversationRun, run_id, with_for_update=True)
                assert locked is not None
                claim_run_owner(locked, worker)
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(transfer())


async def test_gateway_retry_fences_replaced_worker_before_second_send(
    gateway: Gateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions, engine = _postgres_session_factory()
    monkeypatch.setattr(
        "app.services.stream_attempt_owner.tenant_session", partial(tenant_session, maker=sessions)
    )
    monkeypatch.setattr(conversation_stream_service, "async_session", sessions)
    first_attempt_failed = asyncio.Event()
    handoff_committed = asyncio.Event()
    try:
        user_id, agent_id, conversation_id = await _seed_conversation(sessions)
        context = unit_context(user_id)
        registry = RunTaskRegistry(worker_instance_id="same-worker-name")
        async with sessions() as db:
            row = await conversation_run_service.create_run(
                db,
                conversation_id=conversation_id,
                agent_id=agent_id,
                context=context,
                source="chat",
                input_preview="first request",
            )
            await conversation_run_service.transition_run(
                db, row, "running", worker_instance_id=registry.worker_instance_id
            )
            first = claim_run_owner(row, registry.worker_instance_id)
            await db.commit()
        target = RunExecutionTarget(
            first.run_id, RunConversationTarget(conversation_id, agent_id, context)
        )
        stream = conversation_stream_service.prepare_stream_context(
            conversation_id,
            run_id=str(first.run_id),
            tenant=context,
        )
        gateway.respond = lambda: (
            _stream(_error(CONCURRENCY))
            if len(gateway.models) == 1
            else _stream(_chunk(content="stale resend"))
        )

        async def admission() -> None:
            # Pause before the real durable admission on the second attempt.
            if gateway.models:
                first_attempt_failed.set()
                await handoff_committed.wait()
            await check_stream_attempt()

        models = _build_model_candidates(_config())
        middleware = DirectStreamRetryMiddleware(
            fallbacks=models[1:], initial_delay=0, check_authority=admission
        )
        graph = create_agent(model=models[0], middleware=[middleware])

        async def old_worker() -> None:
            with run_fence_scope(first), stream_attempt_owner(target, registry):
                with pytest.raises(RunOwnershipLost):
                    async for _ in _messages(graph, [{"role": "user", "content": "Hi"}]):
                        pass
                with pytest.raises(RunOwnershipLost):
                    await _publish_error(stream, "stale failure")
                with pytest.raises(RunOwnershipLost):
                    await conversation_stream_service.finalize_trace(
                        conversation_id,
                        str(first.run_id),
                        [],
                        [],
                        [],
                        success=False,
                        tenant=context,
                    )

        async with asyncio.timeout(45), asyncio.TaskGroup() as tasks:
            task = tasks.create_task(old_worker())
            registry.start(first.run_id, task)
            await first_attempt_failed.wait()
            replacement = multiprocessing.get_context("spawn").Process(
                target=_replacement_process,
                args=(first.run_id, registry.worker_instance_id),
            )
            replacement.start()
            try:
                await asyncio.to_thread(replacement.join, 30)
                assert replacement.exitcode == 0
            finally:
                if replacement.is_alive():
                    replacement.kill()
                    await asyncio.to_thread(replacement.join, 5)
                replacement.close()
            async with sessions() as db:
                current = await db.get(ConversationRun, first.run_id)
                assert current is not None
                successor = fence_for_run(current)
                assert successor.worker_instance_id == first.worker_instance_id
                assert (
                    successor.owner_token != first.owner_token
                    and successor.generation == first.generation + 1
                )
            handoff_committed.set()

        assert gateway.models == ["gpt-4o-mini"]
        assert stream.broker.last_event_id is None
        with run_fence_scope(successor):
            await stream.persist_cb([{"id": "successor", "event": "status", "data": {}}])
        async with sessions() as db:
            row = await db.get(ConversationRun, first.run_id)
            assert row is not None and row.is_active and row.status == "running"
            assert row.owner_token == successor.owner_token
            trace = await trace_storage.get_trace_by_msg_id(db, str(first.run_id))
            assert trace is not None and trace.status == "streaming"
            assert [event["id"] for event in trace.events] == ["successor"]
    finally:
        handoff_committed.set()
        await engine.dispose()
