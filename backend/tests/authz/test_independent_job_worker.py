"""A committed independent handoff starts through the durable queue and guarded lease."""

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import checkpointer, prepared_run_inputs
from app.agent_runtime.langgraph_agent_stream_runner import resume_agent_stream_langgraph
from app.authz.independent_job_execution import authorize_independent_job_execution
from app.authz.org_context import OrgContext
from app.config import settings
from app.database import Base
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.agent_subagent import AgentSubAgentLink
from app.models.audit_event import AuditEvent
from app.models.conversation_run import ConversationRun, utc_now_naive
from app.models.independent_chat_job import IndependentChatJob
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.memory import UserMemorySettings
from app.models.model import Model
from app.models.organization import Tenant
from app.schemas.independent_chat_job import IndependentJobCreate
from app.services import (
    conversation_run_worker,
    prepared_conversation_executor,
    prepared_conversation_start,
)
from app.services.agent_execution_acceptance import OwnedExecutionRequest, accept_owned_execution
from app.services.conversation_agent_context import resolve_agent_context
from app.services.conversation_run_prepared_recovery import terminalize_orphaned_prepared_runs
from app.services.conversation_run_queue_worker import recover_conversation_queue
from app.services.conversation_run_state import transition_run
from app.services.conversation_run_worker import start_conversation_run
from app.services.independent_job_notifications import reconcile_job_approval
from app.services.independent_jobs import accept_independent_job
from tests.authz import prepared_adapter_testkit as akit
from tests.authz import prepared_materialization_testkit as kit
from tests.authz import prepared_queue_testkit as qkit
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TestSession
from tests.test_agent_lifecycle_run_ownership import _seed_execution_tree

pytestmark = pytest.mark.asyncio


@dataclass
class Handoff:
    job: IndependentChatJob
    context: OrgContext
    factory: kit.ScriptedFactory
    server: kit.FakeMcpServer
    model_name: str


@pytest.fixture
async def handoff(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> Handoff:
    root, child, conversation = await _seed_execution_tree(db)
    model = await db.get(Model, child.model_id)
    assert model is not None
    model.provider = "anthropic"
    child.runtime_policy = {"version": 1}
    with_approval = getattr(request, "param", None) == "approval"
    if with_approval:
        service = McpServer(
            tenant_id=child.tenant_id,
            org_id=child.org_id,
            user_id=child.user_id,
            name="Lookup",
            transport="streamable_http",
            url="https://mcp.test/a",
            is_system=False,
        )
        db.add(service)
        await db.flush()
        tool = McpTool(server_id=service.id, name="lookup", input_schema={"type": "object"})
        db.add(tool)
        await db.flush()
        db.add(AgentMcpToolLink(agent_id=child.id, mcp_tool_id=tool.id))
    db.add(UserMemorySettings(user_id=child.user_id, memory_write_policy="off"))
    await db.commit()
    ctx = unit_context(root.user_id)
    accepted = await accept_owned_execution(
        db, OwnedExecutionRequest(conversation.id, root.id, ctx, "chat", "parent")
    )
    parent = await db.get(ConversationRun, accepted.run_id)
    assert parent is not None
    await transition_run(db, parent, "running", worker_instance_id="parent")
    await transition_run(db, parent, "completed")
    await db.commit()

    monkeypatch.setattr(settings, "independent_chat_jobs_enabled", True)
    monkeypatch.setattr(settings, "chat_work_notifications_enabled", True)
    monkeypatch.setattr(settings, "prepared_run_lease_enabled", False)
    create = IndependentJobCreate(request_id=uuid4(), target_id=child.id, instruction="research")
    handoff = await accept_independent_job(db, conversation, parent.id, create, ctx)
    await db.commit()
    # The accepting process stops before calling the queue's local wake-up.
    kit.fresh_registry(monkeypatch)
    monkeypatch.setattr(conversation_run_worker, "get_checkpointer", checkpointer.get_checkpointer)
    akit.patch_keys(monkeypatch)
    for module in (prepared_conversation_start, prepared_conversation_executor):
        monkeypatch.setattr(module, "prepared_run_root", lambda: tmp_path / "leases")
    call = AIMessage("", tool_calls=[{"name": "lookup", "args": {}, "id": "lookup-1"}])
    factory = kit.ScriptedFactory(
        {model.model_name: [call if with_approval else AIMessage("result")]}
    )
    server = kit.FakeMcpServer()
    monkeypatch.setattr(
        prepared_run_inputs, "materializer_seams", lambda: kit.seams(factory, server)
    )
    return Handoff(handoff.job, ctx, factory, server, model.model_name)


@pytest.mark.parametrize("revoke", [False, True])
async def test_restart_claims_child_only_once_after_parent_finished(
    db: AsyncSession, handoff: Handoff, revoke: bool
) -> None:
    job = handoff.job
    if revoke:
        link = await db.get(AgentSubAgentLink, (job.parent_agent_id, job.child_agent_id))
        assert link is not None
        await db.delete(link)
        await db.commit()
    await recover_conversation_queue()
    child = await qkit.await_run(job.child_run_id)
    if revoke:
        assert child.status == "failed"
        assert not kit.SENDS
    else:
        assert child.status == "completed", child.error_code
        assert child.runtime_materialization_sha256
        assert len(kit.SENDS) == 1
    assert await recover_conversation_queue() == 0
    assert len(kit.SENDS) == int(not revoke)
    outbox = Base.metadata.tables["chat_notification_outbox"]

    # Commit includes exactly one completion intent. Re-polling recovery never
    # creates another child execution or another notice for the same result.
    count = await db.scalar(
        select(func.count())
        .select_from(outbox)
        .where(
            outbox.c.source_id == job.id,
            outbox.c.kind == "chat_task.completed",
        )
    )
    assert count == int(not revoke)


async def test_missing_durable_parent_cannot_turn_child_into_ordinary_chat(
    db: AsyncSession, handoff: Handoff
) -> None:
    job, ctx = handoff.job, handoff.context
    conversation_id, run_id = job.child_conversation_id, job.child_run_id
    await db.delete(job)
    await db.commit()
    with pytest.raises(AppError) as exc:
        await authorize_independent_job_execution(
            db, run_id=run_id, conversation_id=conversation_id, context=ctx
        )
    assert exc.value.code == "independent_job_unavailable"


async def test_completed_child_refuses_new_unrelated_run(
    db: AsyncSession, handoff: Handoff
) -> None:
    job, ctx = handoff.job, handoff.context
    child = await db.get(ConversationRun, job.child_run_id)
    assert child is not None
    await transition_run(db, child, "running", worker_instance_id="test")
    await transition_run(db, child, "completed")
    await db.commit()
    with pytest.raises(HTTPException, match="Independent work"):
        await accept_owned_execution(
            db,
            OwnedExecutionRequest(job.child_conversation_id, job.child_agent_id, ctx, "chat", ""),
        )


async def test_orphaned_materialized_child_is_never_relaunched_with_ordinary_lease_off(
    db: AsyncSession, handoff: Handoff
) -> None:
    job = handoff.job
    await db.execute(
        update(ConversationRun)
        .where(ConversationRun.id == job.child_run_id)
        .values(
            status="running",
            worker_instance_id="lost",
            runtime_materialized_at=utc_now_naive(),
            runtime_materialization_sha256="a" * 64,
            heartbeat_at=utc_now_naive() - timedelta(days=1),
        )
    )
    await db.commit()
    assert await terminalize_orphaned_prepared_runs(
        session_factory=TestSession, stale_before=utc_now_naive() - timedelta(minutes=1)
    ) == [job.child_run_id]
    failed = await qkit.await_run(job.child_run_id)
    assert (failed.status, failed.error_code) == ("failed", "prepared_run_lease_missing")
    assert await recover_conversation_queue() == 0
    assert not kit.SENDS


@pytest.mark.parametrize("handoff", ["approval"], indirect=True)
async def test_child_approves_one_tool_after_parent_finished(
    db: AsyncSession, handoff: Handoff, monkeypatch: pytest.MonkeyPatch
) -> None:
    job, ctx = handoff.job, handoff.context
    # Interrupt the best-effort projection after the child committed "interrupted".
    # The persisted marker and the actual graph checkpoint must survive recovery.
    monkeypatch.setattr(
        "app.services.conversation_run_execution.record_interrupted_run_best_effort", AsyncMock()
    )
    await recover_conversation_queue()
    first = await qkit.await_run(job.child_run_id)
    assert first.status == "interrupted", first.error_code
    assert handoff.server.calls == 0
    handoff.factory.scripts = {handoff.model_name: [AIMessage("lookup result")]}
    resume = OwnedExecutionRequest(
        job.child_conversation_id,
        job.child_agent_id,
        ctx,
        "chat",
        None,
        run_source="resume",
        parent_run_id=first.id,
        interrupt_id=first.interrupt_id,
    )
    with pytest.raises(AppError) as pending:
        await accept_owned_execution(db, resume)
    assert pending.value.code == "independent_job_approval_sync_pending"
    assert pending.value.status == 409 and pending.value.details == {"retryable": True}
    assert handoff.server.calls == 0
    await db.rollback()
    await db.refresh(job)
    assert await reconcile_job_approval(db, job.id, ctx)
    await db.commit()
    accepted = await accept_owned_execution(db, resume)
    await db.commit()
    user = CurrentUser(id=ctx.user_id, email="user@example.test", name="User")
    cfg = await resolve_agent_context(db, job.child_conversation_id, user, context=ctx)
    assert cfg.steer_store is None
    await db.commit()
    await start_conversation_run(
        run_id=accepted.run_id,
        conversation_id=job.child_conversation_id,
        cfg=cfg,
        user=user,
        input_payload={"decisions": [{"type": "approve"}]},
        moldy_source="resume",
        executor_fn=resume_agent_stream_langgraph,
    )
    resumed = await qkit.await_run(accepted.run_id)
    assert resumed.status == "completed", resumed.error_code
    assert resumed.id != first.id and resumed.parent_run_id == first.id
    assert len(kit.SENDS) == 2
    assert handoff.server.calls == 1


async def test_recovered_child_rechecks_new_spend_cap_before_its_first_send(
    db: AsyncSession, handoff: Handoff
) -> None:
    tenant = await db.get(Tenant, handoff.context.tenant_id)
    assert tenant is not None
    # Simulate a different process committing a lower limit. The local
    # below-threshold cache from the original admission remains populated.
    tenant.limits = {**tenant.limits, "monthly_spend_limit": "0"}
    await db.commit()
    await recover_conversation_queue()
    child = await qkit.await_run(handoff.job.child_run_id)
    assert child.status == "failed"
    assert child.error_code == "quota_exceeded"
    assert (
        await db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.run_id == str(child.id), AuditEvent.action == "conversation.run_failed"
            )
        )
        == 1
    )
    assert not kit.SENDS


async def test_active_child_stops_before_another_send_when_parent_delegation_is_revoked(
    db: AsyncSession, handoff: Handoff, monkeypatch: pytest.MonkeyPatch
) -> None:
    # This tool round trip requires a second model call. Revoke the delegation
    # after the first real permit opens and before the next tool or model permit.
    first = AIMessage("", tool_calls=[{"name": "ls", "args": {"path": "/"}, "id": "list-1"}])
    handoff.factory.scripts = {handoff.model_name: [first, AIMessage("should not run")]}
    generate = kit.GuardedScriptedModel._agenerate

    async def revoke_after_send(model: kit.GuardedScriptedModel, *args: Any, **kwargs: Any) -> Any:
        response = await generate(model, *args, **kwargs)
        async with TestSession() as external:
            link = await external.get(
                AgentSubAgentLink, (handoff.job.parent_agent_id, handoff.job.child_agent_id)
            )
            assert link is not None
            await external.delete(link)
            await external.commit()
        return response

    monkeypatch.setattr(kit.GuardedScriptedModel, "_agenerate", revoke_after_send)
    await recover_conversation_queue()
    child = await qkit.await_run(handoff.job.child_run_id)
    assert child.status == "failed", child.error_code
    assert [handoff.model_name] == kit.SENDS
    assert await recover_conversation_queue() == 0
