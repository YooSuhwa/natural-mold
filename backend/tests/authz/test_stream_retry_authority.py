"""Pending model attempts respect the durable owner, including inside child tasks."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from sqlalchemy import update

from app.agent_runtime.stream_retry_authority import StreamAttemptDenied, check_stream_attempt
from app.dependencies import CurrentUser
from app.models.conversation_run import ConversationRun
from app.models.organization import Organization
from app.services.agent_execution_runtime import run_owned_execution
from app.services.conversation_run_worker import (
    RunTaskRegistry,
    get_run_task_registry,
    start_conversation_run,
)
from tests.authz.agent_execution_runtime_fixture import runtime_request, seed_runtime_run
from tests.authz.run_fixture_config import run_fixture_config
from tests.conftest import TestSession


@pytest.fixture(autouse=True)
def use_test_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.database.async_session", TestSession)
    # Isolate the provider preflight from the separate worker cancellation poller.
    monkeypatch.setattr("app.services.agent_execution_runtime._CANCEL_POLL_SECONDS", 60)


@pytest.mark.parametrize("prepared", [True, False])
@pytest.mark.parametrize("change", ["stop", "handover", "finished"])
async def test_next_attempt_observes_changed_owner_state(prepared: bool, change: str) -> None:
    run, agent, conversation = await seed_runtime_run()

    async def attempt() -> None:
        # The actual model node may be a different task than the registered worker.
        await asyncio.create_task(check_stream_attempt(prepared=prepared))
        changes = {
            "stop": {"cancel_requested_at": datetime.now(UTC).replace(tzinfo=None)},
            "handover": {"worker_instance_id": "next-worker"},
            "finished": {"status": "completed", "is_active": False},
        }
        async with TestSession() as db:
            await db.execute(
                update(ConversationRun)
                .where(ConversationRun.id == run.id)
                .values(**changes[change])
            )
            await db.commit()
        error = asyncio.CancelledError if change == "stop" else StreamAttemptDenied
        with pytest.raises(error):
            await asyncio.create_task(check_stream_attempt(prepared=prepared))

    # Using a real owned body proves that the context is wired into supervision.
    # Restore the owner state only so the supervision fixture can finalize it.
    async def operation() -> None:
        await attempt()
        async with TestSession() as db:
            await db.execute(
                update(ConversationRun)
                .where(ConversationRun.id == run.id)
                .values(
                    status="running",
                    is_active=True,
                    cancel_requested_at=None,
                    worker_instance_id=get_run_task_registry().worker_instance_id,
                )
            )
            await db.commit()

    await run_owned_execution(runtime_request(run, agent, conversation), operation)
    await check_stream_attempt()  # Context was reset by the owner.


async def test_prepared_archive_authority_stays_with_its_dispatch_permit() -> None:
    run, agent, conversation = await seed_runtime_run()

    async def operation() -> None:
        async with TestSession() as db:
            await db.execute(
                update(Organization)
                .where(Organization.id == agent.org_id)
                .values(status="archived", archived_at=datetime.now(UTC))
            )
            await db.commit()
        with pytest.raises(StreamAttemptDenied):
            await check_stream_attempt()
        await check_stream_attempt(prepared=True)
        async with TestSession() as db:
            await db.execute(
                update(Organization)
                .where(Organization.id == agent.org_id)
                .values(status="active", archived_at=None)
            )
            await db.commit()

    await run_owned_execution(runtime_request(run, agent, conversation), operation)


async def test_standalone_invocation_does_not_open_a_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(*_args: object) -> None:
        pytest.fail("standalone model has no claimed durable run")

    monkeypatch.setattr("app.services.stream_attempt_owner.tenant_session", unexpected)
    await check_stream_attempt()


async def test_ordinary_chat_worker_checks_remote_stop_before_resend() -> None:
    run, _, conversation = await seed_runtime_run()
    admitted: list[str] = []

    async def stream(*_args: object, **_kwargs: object) -> AsyncGenerator[str, None]:
        await check_stream_attempt()
        admitted.append("first")
        async with TestSession() as db:
            await db.execute(
                update(ConversationRun)
                .where(ConversationRun.id == run.id)
                .values(
                    status="canceling", cancel_requested_at=datetime.now(UTC).replace(tzinfo=None)
                )
            )
            await db.commit()
        await check_stream_attempt()
        admitted.append("second")
        yield "unexpected second send"

    registry = RunTaskRegistry(worker_instance_id="test-chat-worker")
    await start_conversation_run(
        run_id=run.id,
        conversation_id=conversation.id,
        cfg=await run_fixture_config(run.id, TestSession),
        user=CurrentUser(id=run.user_id, email="test@example.com", name="Test"),
        input_payload={"content": "question"},
        moldy_source="chat",
        executor_fn=stream,
        registry=registry,
    )
    task = registry.get(run.id)
    assert task is not None
    await task
    assert admitted == ["first"]
    async with TestSession() as db:
        stored = await db.get(ConversationRun, run.id)
        assert stored is not None and stored.status == "canceled"
