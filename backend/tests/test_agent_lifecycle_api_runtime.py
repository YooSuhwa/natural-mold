from __future__ import annotations

import anyio
import pytest
from sqlalchemy import select

from app.agent_api.dependencies import ApiKeyPrincipal
from app.authz.api_key_actor import resolve_api_key_actor
from app.models.agent_api import AgentApiRun
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.conversation_run import ConversationRun
from app.routers.agent_runtime_api import _run_stream_response
from app.schemas.agent_api import AgentRunInput, AgentRunRequest
from tests.agent_lifecycle_api_test_support import (
    ApiRuntimeCase,
    CompatRequest,
    CompatSurface,
    api_runtime_case,
    consume_until,
    post_compat_surface,
)
from tests.test_agent_runtime_public_api import _seed_deployed_agent_with_key

pytestmark = pytest.mark.anyio
__all__ = ["api_runtime_case"]


async def test_wait_run_accepts_one_durable_owner_before_execution(client, db, monkeypatch) -> None:
    agent, deployment, _key, cleartext = await _seed_deployed_agent_with_key(db)

    async def fake_invoke(_cfg, _messages, **_kwargs):
        return "owned answer"

    monkeypatch.setattr(
        "app.agent_api.runtime_service.execute_agent_invoke",
        fake_invoke,
    )

    response = await client.post(
        "/v1/runs/wait",
        headers={"Authorization": f"Bearer {cleartext}"},
        json={
            "agent_id": deployment.public_id,
            "input": {"messages": [{"role": "user", "content": "Hello"}]},
        },
    )

    assert response.status_code == 200, response.text
    envelope = await db.scalar(select(AgentApiRun))
    assert envelope is not None
    assert envelope.conversation_run_id is not None
    durable_run = await db.get(ConversationRun, envelope.conversation_run_id)
    assert durable_run is not None
    assert durable_run.status == "completed"
    participants = tuple(
        await db.scalars(
            select(ConversationRunAgent).where(ConversationRunAgent.run_id == durable_run.id)
        )
    )
    assert [(row.agent_id_snapshot, row.role) for row in participants] == [(agent.id, "root")]
    assert response.json()["status"] == "succeeded"
    assert response.json()["output"] == {"answer": "owned answer"}


async def test_archive_before_stream_first_iteration_skips_executor_and_acks(
    client, db, monkeypatch
) -> None:
    agent, deployment, key, _cleartext = await _seed_deployed_agent_with_key(db)
    context = await resolve_api_key_actor(db, key)
    principal = ApiKeyPrincipal(key=key, user_id=key.user_id, context=context)
    executor_calls = 0

    async def sentinel_stream(_cfg, _messages, **_kwargs):
        nonlocal executor_calls
        executor_calls += 1
        yield 'event: content_delta\ndata: {"content":"forbidden"}\n\n'

    monkeypatch.setattr("app.agent_api.runtime_service.execute_agent_stream", sentinel_stream)
    response = await _run_stream_response(
        request=AgentRunRequest(
            agent_id=deployment.public_id,
            input=AgentRunInput(messages=[{"role": "user", "content": "Hello"}]),
        ),
        db=db,
        principal=principal,
        thread_id=None,
    )
    archive = await client.delete(f"/api/agents/{agent.id}")
    assert archive.status_code == 204, archive.text

    async for _chunk in response.body_iterator:
        pass

    assert executor_calls == 0
    db.expire_all()
    envelope = await db.scalar(select(AgentApiRun))
    assert envelope is not None
    assert envelope.status == "canceled"
    assert envelope.conversation_run_id is not None
    durable_run = await db.get(ConversationRun, envelope.conversation_run_id)
    assert durable_run is not None
    assert durable_run.status == "canceled"
    assert durable_run.cancellation_acknowledged_at is not None


async def test_archive_after_stream_launch_cancels_actual_owner_and_acks(
    client, db, monkeypatch
) -> None:
    agent, deployment, key, _cleartext = await _seed_deployed_agent_with_key(db)
    context = await resolve_api_key_actor(db, key)
    principal = ApiKeyPrincipal(key=key, user_id=key.user_id, context=context)
    operation_started = anyio.Event()
    operation_stopped = anyio.Event()
    owner_cancelled = anyio.Event()

    async def blocking_stream(_cfg, _messages, **_kwargs):
        operation_started.set()
        try:
            await anyio.sleep_forever()
        finally:
            operation_stopped.set()
        yield "unreachable"

    monkeypatch.setattr("app.agent_api.runtime_service.execute_agent_stream", blocking_stream)
    response = await _run_stream_response(
        request=AgentRunRequest(
            agent_id=deployment.public_id,
            input=AgentRunInput(messages=[{"role": "user", "content": "Hello"}]),
        ),
        db=db,
        principal=principal,
        thread_id=None,
    )

    async def consume() -> None:
        async for _chunk in response.body_iterator:
            pass

    async def consume_cancel() -> None:
        try:
            await consume()
        except anyio.get_cancelled_exc_class():
            owner_cancelled.set()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(consume_cancel)
        await operation_started.wait()
        archive = await client.delete(f"/api/agents/{agent.id}")
        assert archive.status_code == 204, archive.text
        await operation_stopped.wait()
        await owner_cancelled.wait()

    db.expire_all()
    envelope = await db.scalar(select(AgentApiRun))
    assert envelope is not None and envelope.conversation_run_id is not None
    durable_run = await db.get(ConversationRun, envelope.conversation_run_id)
    assert envelope.status == "canceled"
    assert durable_run is not None and durable_run.status == "canceled"
    assert durable_run.worker_instance_id is not None
    assert durable_run.cancellation_acknowledged_at is not None


async def test_stream_consumer_close_persists_cancel_and_owner_ack(db, monkeypatch) -> None:
    _agent, deployment, key, _cleartext = await _seed_deployed_agent_with_key(db)
    context = await resolve_api_key_actor(db, key)
    principal = ApiKeyPrincipal(key=key, user_id=key.user_id, context=context)
    operation_closed = anyio.Event()

    async def open_stream(_cfg, _messages, **_kwargs):
        try:
            yield 'event: content_delta\ndata: {"content":"owned"}\n\n'
            await anyio.sleep_forever()
        finally:
            operation_closed.set()

    monkeypatch.setattr("app.agent_api.runtime_service.execute_agent_stream", open_stream)
    response = await _run_stream_response(
        request=AgentRunRequest(
            agent_id=deployment.public_id,
            input=AgentRunInput(messages=[{"role": "user", "content": "Hello"}]),
        ),
        db=db,
        principal=principal,
        thread_id=None,
    )
    body = await consume_until(response.body_iterator, "owned")
    await body.aclose()
    await operation_closed.wait()

    db.expire_all()
    envelope = await db.scalar(select(AgentApiRun))
    assert envelope is not None and envelope.conversation_run_id is not None
    durable_run = await db.get(ConversationRun, envelope.conversation_run_id)
    assert envelope.status == "canceled"
    assert durable_run is not None and durable_run.status == "canceled"
    assert durable_run.cancel_requested_at is not None
    assert durable_run.cancellation_acknowledged_at is not None


async def test_internal_stream_error_projects_failed_owner_once(client, db, monkeypatch) -> None:
    _agent, deployment, _key, cleartext = await _seed_deployed_agent_with_key(db)

    async def failed_stream(_cfg, _messages, **_kwargs):
        yield 'event: error\ndata: {"message":"provider failed"}\n\n'

    monkeypatch.setattr("app.agent_api.runtime_service.execute_agent_stream", failed_stream)
    response = await client.post(
        "/v1/runs/stream",
        headers={"Authorization": f"Bearer {cleartext}"},
        json={
            "agent_id": deployment.public_id,
            "input": {"messages": [{"role": "user", "content": "Hello"}]},
        },
    )

    assert response.status_code == 200
    assert response.text.count("Agent run failed") == 1
    db.expire_all()
    envelope = await db.scalar(select(AgentApiRun))
    assert envelope is not None and envelope.conversation_run_id is not None
    durable_run = await db.get(ConversationRun, envelope.conversation_run_id)
    assert envelope.status == "failed"
    assert envelope.error_code == "AGENT_API_STREAM_FAILED"
    assert durable_run is not None and durable_run.status == "failed"


@pytest.mark.parametrize(
    "surface",
    ["native_stream", "openai_stream", "dify_blocking", "thread_wait"],
)
async def test_api_compat_surface_uses_one_durable_owner(
    surface: CompatSurface,
    api_runtime_case: ApiRuntimeCase,
) -> None:
    db = api_runtime_case.db
    monkeypatch = api_runtime_case.monkeypatch
    agent, deployment, _key, cleartext = await _seed_deployed_agent_with_key(db)

    async def fake_invoke(_cfg, _messages, **_kwargs):
        return "owned answer"

    async def fake_stream(_cfg, _messages, **_kwargs):
        yield 'event: content_delta\ndata: {"content":"owned"}\n\n'

    monkeypatch.setattr("app.agent_api.runtime_service.execute_agent_invoke", fake_invoke)
    monkeypatch.setattr("app.agent_api.runtime_service.execute_agent_stream", fake_stream)
    response = await post_compat_surface(
        api_runtime_case,
        CompatRequest(surface, deployment.public_id, cleartext),
    )

    assert response.status_code == 200, response.text
    envelopes = tuple(await db.scalars(select(AgentApiRun)))
    assert len(envelopes) == 1
    assert envelopes[0].conversation_run_id is not None
    durable_runs = tuple(await db.scalars(select(ConversationRun)))
    assert len(durable_runs) == 1
    assert durable_runs[0].status == "completed"
    participants = tuple(await db.scalars(select(ConversationRunAgent)))
    assert [(row.agent_id_snapshot, row.role) for row in participants] == [(agent.id, "root")]
