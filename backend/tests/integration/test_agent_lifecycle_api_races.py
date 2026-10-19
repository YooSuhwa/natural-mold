from __future__ import annotations

from collections.abc import AsyncGenerator

import anyio
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.agent_api import runtime_service
from app.dependencies import CurrentUser
from app.models.agent_api import AgentApiRun
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.user import User
from app.routers.agent_runtime_api import _run_stream_response
from app.schemas.agent_api import AgentRunInput, AgentRunRequest
from app.services import conversation_run_service
from app.services.agent_execution_owner import notify_local_owner
from app.services.agent_lifecycle import archive_agent
from app.services.conversation_run_worker import get_run_task_registry
from tests.authz.agent_lifecycle_fixture import registered_lifecycle_fixture
from tests.integration.agent_lifecycle_api_native_support import (
    NativeSurface,
    NativeSurfaceRequest,
    archive_root,
    invoke_surface,
    load_api_run,
    native_api_harness,
)

pytestmark = pytest.mark.integration


async def test_native_api_archive_after_config_before_wait_invoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with registered_lifecycle_fixture(monkeypatch) as fixture:  # noqa: SIM117
        async with native_api_harness(fixture, monkeypatch) as harness:
            invoked = 0
            original = runtime_service.execute_owned_wait

            async def fake_invoke(_cfg, _messages, **_kwargs):
                nonlocal invoked
                invoked += 1
                return "forbidden"

            async def archive_after_accept(request):
                async with fixture.sessions() as db:
                    owner = await db.get(User, fixture.actors.owner_one_id)
                    assert owner is not None
                    result = await archive_agent(
                        db,
                        CurrentUser(id=owner.id, email=owner.email, name=owner.name),
                        fixture.graph.root_agent_id,
                    )
                    await db.commit()
                for intent in result.cancel_intents:
                    notify_local_owner(intent, get_run_task_registry())
                return await original(request)

            monkeypatch.setattr(runtime_service, "execute_agent_invoke", fake_invoke)
            monkeypatch.setattr(runtime_service, "execute_owned_wait", archive_after_accept)
            async with AsyncClient(
                transport=ASGITransport(app=harness.app), base_url="http://fixture"
            ) as client:
                response = await client.post(
                    "/v1/runs/wait",
                    headers={"Authorization": f"Bearer {harness.cleartext_key}"},
                    json={
                        "agent_id": harness.deployment.public_id,
                        "input": {"messages": [{"role": "user", "content": "native"}]},
                    },
                )
            assert invoked == 0
            assert response.status_code == 409, response.text
            envelope, run = await load_api_run(fixture, "wait")
            assert envelope.status == "canceled"
            assert run.status == "canceled"
            assert run.worker_instance_id is None
            assert run.cancellation_acknowledged_at is not None


async def test_native_api_launch_first_archive_cancels_actual_stream_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with registered_lifecycle_fixture(monkeypatch) as fixture:  # noqa: SIM117
        async with native_api_harness(fixture, monkeypatch) as harness:
            started = anyio.Event()
            stopped = anyio.Event()
            owner_cancelled = anyio.Event()

            async def blocking_stream(_cfg, _messages, **_kwargs):
                fixture.sentinel.provider_invocations += 1
                started.set()
                try:
                    await anyio.sleep_forever()
                finally:
                    stopped.set()
                yield "unreachable"

            monkeypatch.setattr(runtime_service, "execute_agent_stream", blocking_stream)
            async with fixture.sessions() as db:
                response = await _run_stream_response(
                    request=AgentRunRequest(
                        agent_id=harness.deployment.public_id,
                        input=AgentRunInput(messages=[{"role": "user", "content": "native"}]),
                    ),
                    db=db,
                    principal=harness.principal,
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
                    await started.wait()
                    await archive_root(harness)
                    await stopped.wait()
                    await owner_cancelled.wait()

            envelope, run = await load_api_run(fixture, "stream")
            assert fixture.sentinel.provider_invocations == 1
            assert envelope.status == "canceled"
            assert run.status == "canceled"
            assert run.worker_instance_id is not None
            assert run.cancellation_acknowledged_at is not None


async def test_native_api_stream_consumer_close_persists_owner_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with registered_lifecycle_fixture(monkeypatch) as fixture:  # noqa: SIM117
        async with native_api_harness(fixture, monkeypatch) as harness:
            closed = anyio.Event()

            async def open_stream(_cfg, _messages, **_kwargs):
                fixture.sentinel.provider_invocations += 1
                try:
                    yield 'event: content_delta\ndata: {"content":"native"}\n\n'
                    await anyio.sleep_forever()
                finally:
                    closed.set()

            monkeypatch.setattr(runtime_service, "execute_agent_stream", open_stream)
            async with fixture.sessions() as db:
                response = await _run_stream_response(
                    request=AgentRunRequest(
                        agent_id=harness.deployment.public_id,
                        input=AgentRunInput(messages=[{"role": "user", "content": "native"}]),
                    ),
                    db=db,
                    principal=harness.principal,
                    thread_id=None,
                )
                body = response.body_iterator
                assert isinstance(body, AsyncGenerator)
                while True:
                    chunk = await anext(body)
                    found = (
                        "native" in chunk if isinstance(chunk, str) else b"native" in bytes(chunk)
                    )
                    if found:
                        break
                await body.aclose()
                await closed.wait()

            envelope, run = await load_api_run(fixture, "stream")
            assert fixture.sentinel.provider_invocations == 1 and envelope.status == "canceled"
            assert run.status == "canceled"
            assert run.cancel_requested_at is not None
            assert run.cancellation_acknowledged_at is not None


async def test_native_api_remote_owner_archive_retains_canceling_without_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with registered_lifecycle_fixture(monkeypatch) as fixture:  # noqa: SIM117
        async with native_api_harness(fixture, monkeypatch) as harness:
            invoked = 0

            async def forbidden_stream(_cfg, _messages, **_kwargs):
                nonlocal invoked
                invoked += 1
                yield "forbidden"

            monkeypatch.setattr(runtime_service, "execute_agent_stream", forbidden_stream)
            async with fixture.sessions() as db:
                response = await _run_stream_response(
                    request=AgentRunRequest(
                        agent_id=harness.deployment.public_id,
                        input=AgentRunInput(messages=[{"role": "user", "content": "native"}]),
                    ),
                    db=db,
                    principal=harness.principal,
                    thread_id=None,
                )
                envelope = await db.scalar(
                    select(AgentApiRun).where(
                        AgentApiRun.mode == "stream",
                        AgentApiRun.deployment_id == harness.deployment.id,
                    )
                )
                assert envelope is not None and envelope.conversation_run_id is not None
                envelope_id = envelope.id
                durable_run_id = envelope.conversation_run_id
                run = await db.get(ConversationRun, durable_run_id, with_for_update=True)
                assert run is not None
                await conversation_run_service.transition_run(
                    db, run, "running", worker_instance_id="remote-api-worker"
                )
                await db.commit()
                await archive_root(harness)
                async for _chunk in response.body_iterator:
                    pass

            async with fixture.sessions() as db:
                stored_envelope = await db.get(AgentApiRun, envelope_id)
                stored = await db.get(ConversationRun, durable_run_id)
                assert invoked == 0
                assert stored_envelope is not None and stored_envelope.status == "canceling"
                assert stored is not None and stored.status == "canceling"
                assert stored.worker_instance_id == "remote-api-worker"
                assert stored.cancellation_acknowledged_at is None


@pytest.mark.parametrize(
    "surface",
    ["wait", "native_stream", "openai_stream", "dify_blocking", "thread_wait"],
)
async def test_native_api_surfaces_keep_snapshot_after_envelope_detach(
    surface: NativeSurface,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with registered_lifecycle_fixture(monkeypatch) as fixture:  # noqa: SIM117
        async with native_api_harness(fixture, monkeypatch) as harness:

            async def fake_invoke(_cfg, _messages, **_kwargs):
                fixture.sentinel.provider_invocations += 1
                return "native answer"

            async def fake_stream(_cfg, _messages, **_kwargs):
                fixture.sentinel.provider_invocations += 1
                yield 'event: content_delta\ndata: {"content":"native"}\n\n'

            monkeypatch.setattr(runtime_service, "execute_agent_invoke", fake_invoke)
            monkeypatch.setattr(runtime_service, "execute_agent_stream", fake_stream)
            async with AsyncClient(
                transport=ASGITransport(app=harness.app), base_url="http://fixture"
            ) as client:
                response = await invoke_surface(NativeSurfaceRequest(client, harness, surface))
            assert response.status_code == 200, response.text

            async with fixture.sessions() as db:
                envelope = await db.scalar(
                    select(AgentApiRun)
                    .where(AgentApiRun.deployment_id == harness.deployment.id)
                    .order_by(AgentApiRun.created_at.desc())
                )
                assert envelope is not None and envelope.conversation_run_id is not None
                run = await db.get(ConversationRun, envelope.conversation_run_id)
                conversation = await db.get(Conversation, envelope.conversation_id)
                envelope.deployment_id = None
                envelope.api_key_id = None
                await db.commit()
                await db.refresh(envelope)
                assert envelope.status == "succeeded"
                assert envelope.deployment_public_id_snapshot == harness.deployment.public_id
                assert envelope.input == {"messages": [{"role": "user", "content": "native"}]}
                assert envelope.output is not None
                assert run is not None and run.status == "completed"
                assert conversation is not None
                assert conversation.user_id == fixture.actors.owner_one_id
                assert fixture.sentinel.provider_invocations == 1
