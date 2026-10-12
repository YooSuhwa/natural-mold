"""Shared durable runtime ownership for invoke and streaming callers."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping, Sequence
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Connection
from sqlalchemy.engine.interfaces import DBAPICursor, ExecutionContext

from app.authz.org_context import OrgContext
from app.exceptions import AppError
from app.models.conversation_run import ConversationRun
from app.models.organization import OrganizationMember, TenantMember
from app.services import agent_execution_runtime, conversation_run_service
from app.services.agent_execution_runtime import (
    RuntimeOwnershipRequest,
    run_owned_execution,
    stream_owned_execution,
)
from tests.authz.agent_execution_runtime_fixture import runtime_request, seed_runtime_run
from tests.authz.unit_scope_fixture import (
    TEST_ORG_ID,
    TEST_TENANT_ID,
)
from tests.conftest import TestSession, engine


class ClaimFailure(RuntimeError):
    """Injected persistence failure used to verify no synthetic ACK."""


@pytest.mark.asyncio
async def test_invoke_claims_and_completes_the_durable_run() -> None:
    run, agent, conversation = await seed_runtime_run()
    observed = False

    async def operation() -> str:
        nonlocal observed
        observed = True
        async with TestSession() as db:
            claimed = await db.get(ConversationRun, run.id)
            assert claimed is not None and claimed.status == "running"
            assert claimed.worker_instance_id is not None
        return "done"

    result = await run_owned_execution(runtime_request(run, agent, conversation), operation)

    assert result == "done"
    assert observed
    async with TestSession() as db:
        stored = await db.get(ConversationRun, run.id)
        assert stored is not None and stored.status == "completed"
        assert stored.is_active is False


@pytest.mark.asyncio
async def test_stream_claim_is_deferred_until_first_iteration() -> None:
    run, agent, conversation = await seed_runtime_run()

    async def operation() -> AsyncGenerator[str, None]:
        yield "first"

    stream = stream_owned_execution(runtime_request(run, agent, conversation), operation)
    async with TestSession() as db:
        queued = await db.get(ConversationRun, run.id)
        assert queued is not None and queued.status == "queued"

    assert await anext(stream) == "first"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    async with TestSession() as db:
        stored = await db.get(ConversationRun, run.id)
        assert stored is not None and stored.status == "completed"


@pytest.mark.asyncio
async def test_archive_before_claim_never_enters_operation() -> None:
    run, agent, conversation = await seed_runtime_run(archived=True)
    invoked = False

    async with TestSession() as db:
        accepted = await db.get(ConversationRun, run.id, with_for_update=True)
        assert accepted is not None
        await conversation_run_service.request_cancel_run(db, accepted, reason="agent_archived")
        await db.commit()

    async def operation() -> None:
        nonlocal invoked
        invoked = True

    with pytest.raises(Exception, match="보관된 에이전트"):
        await run_owned_execution(runtime_request(run, agent, conversation), operation)

    assert invoked is False
    async with TestSession() as db:
        stored = await db.get(ConversationRun, run.id)
        assert stored is not None and stored.status == "canceled"
        assert stored.cancellation_acknowledged_at is not None


@pytest.mark.asyncio
async def test_archive_before_claim_never_acknowledges_remote_owner() -> None:
    run, agent, conversation = await seed_runtime_run(archived=True)
    async with TestSession() as db:
        accepted = await db.get(ConversationRun, run.id, with_for_update=True)
        assert accepted is not None
        await conversation_run_service.transition_run(
            db, accepted, "running", worker_instance_id="remote-worker"
        )
        await conversation_run_service.request_cancel_run(db, accepted, reason="agent_archived")
        await db.commit()

    async def operation() -> None:
        raise AssertionError("remote work must not execute locally")

    with pytest.raises(Exception, match="보관된 에이전트"):
        await run_owned_execution(runtime_request(run, agent, conversation), operation)

    async with TestSession() as db:
        stored = await db.get(ConversationRun, run.id)
        assert stored is not None and stored.status == "canceling"
        assert stored.worker_instance_id == "remote-worker"
        assert stored.cancellation_acknowledged_at is None


@pytest.mark.asyncio
async def test_prestart_ack_rejects_same_user_stale_cross_scope_without_private_hydration() -> None:
    run, agent, conversation = await seed_runtime_run(archived=True)
    async with TestSession() as db:
        accepted = await db.get(ConversationRun, run.id, with_for_update=True)
        assert accepted is not None
        await conversation_run_service.request_cancel_run(db, accepted, reason="agent_archived")
        await db.commit()

    statements: list[str] = []

    def capture_sql(
        _connection: Connection,
        _cursor: DBAPICursor,
        statement: str,
        _parameters: Sequence[Sequence[str]] | Mapping[str, str],
        _context: ExecutionContext,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", capture_sql)
    stale_request = RuntimeOwnershipRequest(
        run_id=run.id,
        conversation_id=conversation.id,
        root_agent_id=agent.id,
        context=OrgContext(org_id=uuid4(), tenant_id=uuid4(), user_id=agent.user_id),
    )

    async def operation() -> None:
        raise AssertionError("cross-scope operation must not execute")

    try:
        with pytest.raises(AppError):
            await run_owned_execution(stale_request, operation)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture_sql)

    async with TestSession() as db:
        stored = await db.get(ConversationRun, run.id)
        assert stored is not None and stored.status == "canceling"
        assert stored.cancellation_acknowledged_at is None
    run_selects = [
        statement.lower()
        for statement in statements
        if "from conversation_runs" in statement.lower()
    ]
    assert run_selects
    assert all("input_preview" not in statement for statement in run_selects)
    assert all("error_message" not in statement for statement in run_selects)


@pytest.mark.asyncio
async def test_prestart_ack_survives_same_scope_membership_revocation() -> None:
    run, agent, conversation = await seed_runtime_run()
    async with TestSession() as db:
        accepted = await db.get(ConversationRun, run.id, with_for_update=True)
        org_member = await db.get(OrganizationMember, (TEST_ORG_ID, agent.user_id))
        tenant_member = await db.get(TenantMember, (TEST_TENANT_ID, agent.user_id))
        assert accepted is not None and org_member is not None and tenant_member is not None
        await conversation_run_service.request_cancel_run(db, accepted, reason="membership_revoked")
        org_member.status = "removed"
        tenant_member.status = "removed"
        await db.commit()

    invoked = False

    async def operation() -> None:
        nonlocal invoked
        invoked = True

    with pytest.raises(AppError):
        await run_owned_execution(runtime_request(run, agent, conversation), operation)

    assert invoked is False
    async with TestSession() as db:
        stored = await db.get(ConversationRun, run.id)
        assert stored is not None and stored.status == "canceled"
        assert stored.cancellation_acknowledged_at is not None


@pytest.mark.asyncio
async def test_unexpected_claim_failure_never_acknowledges_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, agent, conversation = await seed_runtime_run()
    async with TestSession() as db:
        accepted = await db.get(ConversationRun, run.id, with_for_update=True)
        assert accepted is not None
        await conversation_run_service.request_cancel_run(db, accepted, reason="stop")
        await db.commit()

    async def broken_claim(_request: RuntimeOwnershipRequest) -> None:
        raise ClaimFailure("database unavailable")

    monkeypatch.setattr(agent_execution_runtime, "_claim", broken_claim)

    async def operation() -> None:
        raise AssertionError("claim failure must not execute")

    with pytest.raises(ClaimFailure, match="database unavailable"):
        await run_owned_execution(runtime_request(run, agent, conversation), operation)

    async with TestSession() as db:
        stored = await db.get(ConversationRun, run.id)
        assert stored is not None and stored.status == "canceling"
        assert stored.cancellation_acknowledged_at is None
