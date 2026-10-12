from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Literal

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.models.agent import Agent
from app.models.agent_api import AgentApiRun
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.audit_event import AuditEvent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_metrics import ConversationRunMetrics
from app.models.message_event import MessageEvent, MessageEventChunk
from app.models.model import Model
from app.models.token_usage import TokenUsage
from app.models.user import User
from app.services import agent_execution_owner, conversation_run_service, conversation_run_worker
from app.services.agent_execution_owner import (
    ExecutionEnvelopeRef,
    OwnedExecutionRequest,
    OwnedExecutionSource,
)
from app.services.conversation_run_worker import RunTaskRegistry
from tests.authz.run_fixture_config import run_fixture_config
from tests.authz.unit_scope_fixture import (
    TEST_ORG_ID,
    TEST_TENANT_ID,
    ensure_unit_scope,
    unit_context,
)
from tests.conftest import TEST_USER_ID, TestSession, engine


async def _seed_execution_tree(db: AsyncSession) -> tuple[Agent, Agent, Conversation]:
    user = User(id=TEST_USER_ID, email="owner@task6.test", name="Task 6 Owner")
    db.add(user)
    model = Model(provider="openai", model_name="task6-model", display_name="Task 6")
    db.add(model)
    await db.flush()
    await ensure_unit_scope(db, CurrentUser(id=user.id, email=user.email, name=user.name))
    root = Agent(
        user_id=user.id,
        name="Root",
        system_prompt="root",
        model_id=model.id,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
    )
    child = Agent(
        user_id=user.id,
        name="Child",
        system_prompt="child",
        model_id=model.id,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
    )
    db.add_all([root, child])
    await db.flush()
    db.add(AgentSubAgentLink(parent_agent_id=root.id, sub_agent_id=child.id, position=0))
    conversation = Conversation(
        agent_id=root.id,
        user_id=user.id,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        agent_name_snapshot=root.name,
        title="Task 6",
        last_activity_at=datetime.now(UTC),
    )
    db.add(conversation)
    await db.flush()
    return root, child, conversation


async def _skip_sqlite_advisory_locks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_op(_db: AsyncSession, _agent_ids: object) -> None:
        return None

    monkeypatch.setattr(
        "app.services.agent_lifecycle_locks.acquire_agent_lifecycle_locks",
        no_op,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["chat", "api", "trigger"])
async def test_all_sources_accept_one_run_complete_participants_and_exact_envelope(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    source: OwnedExecutionSource,
) -> None:
    await _skip_sqlite_advisory_locks(monkeypatch)
    root, child, conversation = await _seed_execution_tree(db)
    envelope: ExecutionEnvelopeRef | None = None
    if source == "api":
        api = AgentApiRun(
            public_id=f"run-{uuid.uuid4()}",
            user_id=root.user_id,
            deployment_id=None,
            deployment_public_id_snapshot="deployment-snapshot",
            conversation_id=conversation.id,
            mode="background",
            status="pending",
        )
        db.add(api)
        await db.flush()
        envelope = ExecutionEnvelopeRef("api", api.id)
    elif source == "trigger":
        trigger = AgentTriggerRun(
            trigger_id=None,
            agent_id=root.id,
            trigger_name_snapshot="trigger-snapshot",
            user_id=root.user_id,
            conversation_id=conversation.id,
            status="queued",
            source="scheduled",
            input_message="run",
        )
        db.add(trigger)
        await db.flush()
        envelope = ExecutionEnvelopeRef("trigger", trigger.id)

    accepted = await agent_execution_owner.accept_owned_execution(
        db,
        OwnedExecutionRequest(
            conversation_id=conversation.id,
            root_agent_id=root.id,
            context=unit_context(root.user_id),
            source=source,
            input_preview="task6",
            envelope=envelope,
        ),
    )
    await db.commit()

    rows = list(
        (
            await db.scalars(
                select(ConversationRunAgent)
                .where(ConversationRunAgent.run_id == accepted.run_id)
                .order_by(ConversationRunAgent.role, ConversationRunAgent.agent_id_snapshot)
            )
        ).all()
    )
    assert {row.agent_id_snapshot for row in rows} == {root.id, child.id}
    assert [(row.agent_id_snapshot, row.role, row.path) for row in rows] == [
        (root.id, "root", []),
        (child.id, "subagent", [str(root.id), str(child.id)]),
    ]
    assert (
        await db.scalar(
            select(func.count())
            .select_from(ConversationRun)
            .where(ConversationRun.id == accepted.run_id)
        )
        == 1
    )
    durable_run = await db.get(ConversationRun, accepted.run_id)
    assert durable_run is not None
    assert durable_run.source == ("chat" if source == "chat" else "start")
    if source == "api":
        linked = await db.get(AgentApiRun, envelope.envelope_id if envelope else None)
        assert linked is not None and linked.conversation_run_id == accepted.run_id
        assert linked.status == "pending"
    elif source == "trigger":
        linked = await db.get(AgentTriggerRun, envelope.envelope_id if envelope else None)
        assert linked is not None and linked.conversation_run_id == accepted.run_id
        assert linked.status == "queued"
    if envelope is not None:
        run = await db.get(ConversationRun, accepted.run_id, with_for_update=True)
        assert run is not None
        await conversation_run_service.transition_run(
            db, run, "running", worker_instance_id="projection-owner"
        )
        await db.flush()
        projected = await db.get(
            AgentApiRun if source == "api" else AgentTriggerRun,
            envelope.envelope_id,
            populate_existing=True,
        )
        assert projected is not None and projected.status == "running"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing", "wrong_kind", "wrong_conversation"])
async def test_invalid_external_envelope_rejects_before_run_or_participant_effects(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    await _skip_sqlite_advisory_locks(monkeypatch)
    root, _child, conversation = await _seed_execution_tree(db)
    envelope: ExecutionEnvelopeRef | None = None
    persisted: AgentApiRun | None = None
    if case == "wrong_kind":
        envelope = ExecutionEnvelopeRef("trigger", uuid.uuid4())
    elif case == "wrong_conversation":
        other = Conversation(
            agent_id=root.id,
            user_id=root.user_id,
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            agent_name_snapshot=root.name,
            title="Wrong envelope conversation",
            last_activity_at=datetime.now(UTC),
        )
        db.add(other)
        await db.flush()
        persisted = AgentApiRun(
            public_id=f"wrong-{uuid.uuid4()}",
            user_id=root.user_id,
            deployment_id=None,
            deployment_public_id_snapshot="wrong",
            conversation_id=other.id,
            mode="background",
            status="pending",
        )
        db.add(persisted)
        await db.flush()
        envelope = ExecutionEnvelopeRef("api", persisted.id)

    with pytest.raises(ValueError, match="envelope"):
        await agent_execution_owner.accept_owned_execution(
            db,
            OwnedExecutionRequest(
                conversation_id=conversation.id,
                root_agent_id=root.id,
                context=unit_context(root.user_id),
                source="api",
                input_preview="invalid",
                envelope=envelope,
            ),
        )

    assert await db.scalar(select(func.count()).select_from(ConversationRun)) == 0
    assert await db.scalar(select(func.count()).select_from(ConversationRunAgent)) == 0
    if persisted is not None:
        assert persisted.conversation_run_id is None


def _private_ownership_selects(statements: list[str]) -> list[str]:
    private_columns = (
        "conversation_runs.input_preview",
        "conversation_runs.metadata_json",
        "agent_api_runs.input",
        "agent_api_runs.output",
        "agent_api_runs.metadata",
        "agent_api_runs.error_message",
        "agent_trigger_runs.input_message",
        "agent_trigger_runs.output_preview",
        "agent_trigger_runs.error_message",
    )
    return [
        statement
        for statement in statements
        if statement.lstrip().lower().startswith("select")
        and any(column in statement.lower() for column in private_columns)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["api", "trigger"])
async def test_cancel_and_projection_lock_only_metadata_columns(
    db: AsyncSession,
    kind: Literal["api", "trigger"],
) -> None:
    root, _child, conversation = await _seed_execution_tree(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=root.id,
        context=unit_context(root.user_id),
        source="start",
        input_preview="PRIVATE RUN INPUT",
    )
    run.metadata_json = {"secret": "PRIVATE RUN METADATA"}
    if kind == "api":
        envelope: AgentApiRun | AgentTriggerRun = AgentApiRun(
            public_id=f"metadata-{uuid.uuid4()}",
            user_id=root.user_id,
            deployment_id=None,
            deployment_public_id_snapshot="metadata",
            conversation_run_id=run.id,
            conversation_id=conversation.id,
            mode="background",
            status="pending",
            input={"secret": "PRIVATE API INPUT"},
            output={"secret": "PRIVATE API OUTPUT"},
            meta={"secret": "PRIVATE API METADATA"},
        )
    else:
        envelope = AgentTriggerRun(
            agent_id=root.id,
            trigger_name_snapshot="metadata",
            conversation_run_id=run.id,
            user_id=root.user_id,
            conversation_id=conversation.id,
            status="queued",
            input_message="PRIVATE TRIGGER INPUT",
            output_preview="PRIVATE TRIGGER OUTPUT",
        )
    db.add(envelope)
    await db.commit()
    statements: list[str] = []

    def capture_statement(_conn, _cursor, statement, _parameters, _context, _many) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", capture_statement)
    try:
        async with TestSession() as cold_db:
            intent = await agent_execution_owner.stage_cancel_intent(
                cold_db,
                agent_execution_owner.CancelIntentRequest(run.id, "agent_archived"),
            )
            await cold_db.commit()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture_statement)

    assert intent.status == "canceling"
    assert _private_ownership_selects(statements) == []
    async with TestSession() as verify_db:
        stored_run = await verify_db.get(ConversationRun, run.id)
        assert stored_run is not None and stored_run.status == "canceling"
        assert stored_run.input_preview == "PRIVATE RUN INPUT"
        assert stored_run.metadata_json == {"secret": "PRIVATE RUN METADATA"}
        if kind == "api":
            stored_api = await verify_db.get(AgentApiRun, envelope.id)
            assert stored_api is not None and stored_api.status == "canceling"
            assert stored_api.input == {"secret": "PRIVATE API INPUT"}
            assert stored_api.output == {"secret": "PRIVATE API OUTPUT"}
        else:
            stored_trigger = await verify_db.get(AgentTriggerRun, envelope.id)
            assert stored_trigger is not None and stored_trigger.status == "canceling"
            assert stored_trigger.input_message == "PRIVATE TRIGGER INPUT"
            assert stored_trigger.output_preview == "PRIVATE TRIGGER OUTPUT"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["api", "trigger"])
async def test_foreign_envelope_prevalidation_reads_only_metadata(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    kind: Literal["api", "trigger"],
) -> None:
    await _skip_sqlite_advisory_locks(monkeypatch)
    root, _child, conversation = await _seed_execution_tree(db)
    foreign_conversation = Conversation(
        agent_id=root.id,
        user_id=root.user_id,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        agent_name_snapshot=root.name,
        title="Foreign envelope",
        last_activity_at=datetime.now(UTC),
    )
    db.add(foreign_conversation)
    await db.flush()
    if kind == "api":
        envelope: AgentApiRun | AgentTriggerRun = AgentApiRun(
            public_id=f"foreign-{uuid.uuid4()}",
            user_id=root.user_id,
            deployment_id=None,
            deployment_public_id_snapshot="foreign",
            conversation_id=foreign_conversation.id,
            mode="background",
            status="pending",
            input={"secret": "PRIVATE API INPUT"},
            output={"secret": "PRIVATE API OUTPUT"},
            meta={"secret": "PRIVATE API METADATA"},
        )
    else:
        envelope = AgentTriggerRun(
            agent_id=root.id,
            trigger_name_snapshot="foreign",
            user_id=root.user_id,
            conversation_id=foreign_conversation.id,
            status="queued",
            input_message="PRIVATE TRIGGER INPUT",
            output_preview="PRIVATE TRIGGER OUTPUT",
        )
    db.add(envelope)
    await db.commit()
    statements: list[str] = []

    def capture_statement(_conn, _cursor, statement, _parameters, _context, _many) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", capture_statement)
    try:
        async with TestSession() as cold_db:
            with pytest.raises(ValueError, match="accepted conversation"):
                await agent_execution_owner.accept_owned_execution(
                    cold_db,
                    OwnedExecutionRequest(
                        conversation_id=conversation.id,
                        root_agent_id=root.id,
                        context=unit_context(root.user_id),
                        source=kind,
                        input_preview="invalid",
                        envelope=ExecutionEnvelopeRef(kind, envelope.id),
                    ),
                )
            await cold_db.rollback()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture_statement)

    assert _private_ownership_selects(statements) == []
    async with TestSession() as verify_db:
        assert await verify_db.scalar(select(func.count()).select_from(ConversationRun)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("run_status", "api_status", "trigger_status"),
    [
        ("queued", "pending", "queued"),
        ("running", "running", "running"),
        ("canceling", "canceling", "canceling"),
        ("completed", "succeeded", "success"),
        ("canceled", "canceled", "canceled"),
        ("failed", "failed", "failed"),
        ("interrupted", "failed", "failed"),
        ("stale", "failed", "failed"),
    ],
)
async def test_envelope_projection_is_deterministic(
    run_status: str,
    api_status: str,
    trigger_status: str,
) -> None:
    assert agent_execution_owner.api_status_for_run(run_status) == api_status
    assert agent_execution_owner.trigger_status_for_run(run_status) == trigger_status


@pytest.mark.asyncio
async def test_foreign_owner_never_acknowledges_cancellation(db: AsyncSession) -> None:
    root, _child, conversation = await _seed_execution_tree(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=root.id,
        context=unit_context(root.user_id),
        source="chat",
        input_preview="foreign",
    )
    await conversation_run_service.transition_run(
        db, run, "running", worker_instance_id="remote-owner"
    )
    await conversation_run_service.request_cancel_run(db, run, reason="agent_archived")

    with pytest.raises(ValueError, match="owning worker"):
        await conversation_run_service.transition_run(
            db, run, "canceled", cancellation_ack_worker_id="foreign-worker"
        )
    assert run.status == "canceling"
    assert run.cancellation_acknowledged_at is None
    await db.rollback()


@pytest.mark.asyncio
async def test_local_registry_absent_leaves_durable_cancel_pending(
    db: AsyncSession,
) -> None:
    root, _child, conversation = await _seed_execution_tree(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=root.id,
        context=unit_context(root.user_id),
        source="chat",
        input_preview="local_registry_absent",
    )
    await conversation_run_service.transition_run(
        db, run, "running", worker_instance_id="remote-owner"
    )
    await db.commit()

    intent = await agent_execution_owner.persist_cancel_intent(
        TestSession,
        agent_execution_owner.CancelIntentRequest(run.id, "agent_archived"),
    )
    registry = RunTaskRegistry(worker_instance_id="remote-owner")
    assert agent_execution_owner.notify_local_owner(intent, registry) is False

    await db.refresh(run)
    assert run.status == "canceling"
    assert run.cancellation_acknowledged_at is None


@pytest.mark.asyncio
async def test_local_owner_shutdown_timeout_leaves_durable_cancel_unacknowledged(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    root, _child, conversation = await _seed_execution_tree(db)
    registry = RunTaskRegistry(worker_instance_id="local-owner")
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=root.id,
        context=unit_context(root.user_id),
        source="chat",
        input_preview="real shutdown timeout",
    )
    await conversation_run_service.transition_run(
        db, run, "running", worker_instance_id=registry.worker_instance_id
    )
    await db.commit()

    intent = await agent_execution_owner.persist_cancel_intent(
        TestSession,
        agent_execution_owner.CancelIntentRequest(run.id, "agent_archived"),
    )
    task_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def held_local_task() -> None:
        task_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleanup_started.set()
            await asyncio.shield(release_cleanup.wait())

    async def skip_shutdown_intent(_run_ids: list[uuid.UUID]) -> None:
        return None

    async def release_held_cleanup() -> None:
        await asyncio.sleep(0.03)
        release_cleanup.set()

    task = asyncio.create_task(held_local_task())
    registry.start(run.id, task)
    await task_started.wait()
    assert agent_execution_owner.notify_local_owner(intent, registry) is True
    await cleanup_started.wait()
    monkeypatch.setattr(conversation_run_worker, "_persist_shutdown_intent", skip_shutdown_intent)
    caplog.set_level(logging.WARNING, logger=conversation_run_worker.__name__)

    release_task = asyncio.create_task(release_held_cleanup())
    await registry.shutdown(timeout_seconds=0.005)
    await release_task
    task_result = await asyncio.gather(task, return_exceptions=True)

    assert "timed out waiting for 1 conversation run task(s) during shutdown" in caplog.text
    assert isinstance(task_result[0], asyncio.CancelledError)
    assert registry.get(run.id) is None
    await db.refresh(run)
    assert run.status == "canceling"
    assert run.worker_instance_id == registry.worker_instance_id
    assert run.cancellation_acknowledged_at is None


@pytest.mark.asyncio
async def test_workerless_ack_requires_null_owner(db: AsyncSession) -> None:
    root, _child, conversation = await _seed_execution_tree(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=root.id,
        context=unit_context(root.user_id),
        source="chat",
        input_preview="workerless",
    )
    await conversation_run_service.request_cancel_run(db, run)
    await conversation_run_service.transition_run(
        db, run, "canceled", allow_workerless_cancellation_ack=True
    )
    assert run.cancellation_acknowledged_at is not None


@pytest.mark.asyncio
async def test_workerless_terminal_finalization_writes_one_event_audit_and_metrics(
    db: AsyncSession,
) -> None:
    root, _child, conversation = await _seed_execution_tree(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=root.id,
        context=unit_context(root.user_id),
        source="chat",
        input_preview="terminal-once",
    )
    await conversation_run_service.request_cancel_run(db, run)
    await db.commit()
    run_id = run.id

    executor_called = False

    async def never_dispatch(*_args: object, **_kwargs: object):
        nonlocal executor_called
        executor_called = True
        if False:
            yield ""

    registry = RunTaskRegistry(worker_instance_id="terminal-owner")
    from app.services import conversation_run_worker

    await conversation_run_worker.start_conversation_run(
        run_id=run_id,
        conversation_id=conversation.id,
        cfg=await run_fixture_config(run_id, TestSession),
        user=CurrentUser(id=root.user_id, email="owner@task6.test", name="Task 6 Owner"),
        input_payload={"content": "never dispatched"},
        moldy_source="chat",
        executor_fn=never_dispatch,
        registry=registry,
    )
    task = registry.get(run_id)
    assert task is not None
    await task

    assert executor_called is False
    async with TestSession() as verify_db:
        assert (
            await verify_db.scalar(
                select(func.count())
                .select_from(ConversationRunMetrics)
                .where(ConversationRunMetrics.run_id == run_id)
            )
            == 1
        )
        assert (
            await verify_db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.run_id == str(run_id),
                    AuditEvent.action == "conversation.run_canceled",
                )
            )
            == 1
        )
        assert (
            await verify_db.scalar(
                select(func.count())
                .select_from(MessageEvent)
                .where(MessageEvent.assistant_msg_id == str(run_id))
            )
            == 1
        )
        assert (
            await verify_db.scalar(
                select(func.count())
                .select_from(MessageEventChunk)
                .where(MessageEventChunk.assistant_msg_id == str(run_id))
            )
            == 1
        )


@pytest.mark.asyncio
async def test_legacy_unlinked_active_envelopes_are_stopping_conflicts(
    db: AsyncSession,
) -> None:
    root, _child, conversation = await _seed_execution_tree(db)
    api = AgentApiRun(
        public_id=f"legacy-{uuid.uuid4()}",
        user_id=root.user_id,
        deployment_id=None,
        deployment_public_id_snapshot="legacy",
        conversation_id=conversation.id,
        mode="background",
        status="running",
    )
    trigger = AgentTriggerRun(
        trigger_id=None,
        agent_id=root.id,
        trigger_name_snapshot="legacy",
        user_id=root.user_id,
        conversation_id=conversation.id,
        status="running",
        source="scheduled",
        input_message="legacy",
    )
    db.add_all([api, trigger])
    await db.flush()

    conflicts = await agent_execution_owner.active_unlinked_envelopes(db, (root.id,))
    assert conflicts.api_run_ids == (api.id,)
    assert conflicts.trigger_run_ids == (trigger.id,)


@pytest.mark.asyncio
async def test_participant_cancel_excludes_unrelated_child_standalone_run(
    db: AsyncSession,
) -> None:
    root, child, conversation = await _seed_execution_tree(db)
    parent_run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=root.id,
        context=unit_context(root.user_id),
        source="chat",
        input_preview="parent",
    )
    db.add_all(
        [
            ConversationRunAgent(
                run_id=parent_run.id,
                agent_id=root.id,
                agent_id_snapshot=root.id,
                role="root",
                path=[],
            ),
            ConversationRunAgent(
                run_id=parent_run.id,
                agent_id=child.id,
                agent_id_snapshot=child.id,
                role="subagent",
                path=[str(root.id), str(child.id)],
            ),
        ]
    )
    child_conversation = Conversation(
        agent_id=child.id,
        user_id=child.user_id,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        agent_name_snapshot=child.name,
        title="Standalone child",
        last_activity_at=datetime.now(UTC),
    )
    db.add(child_conversation)
    await db.flush()
    child_run = await conversation_run_service.create_run(
        db,
        conversation_id=child_conversation.id,
        agent_id=child.id,
        context=unit_context(child.user_id),
        source="chat",
        input_preview="standalone",
    )
    db.add(
        ConversationRunAgent(
            run_id=child_run.id,
            agent_id=child.id,
            agent_id_snapshot=child.id,
            role="root",
            path=[],
        )
    )
    await db.flush()

    intents = await agent_execution_owner.stage_participant_cancel_intents(
        db,
        agent_execution_owner.ParticipantCancelRequest(root.id, "agent_archived"),
    )

    assert [intent.run_id for intent in intents] == [parent_run.id]
    assert parent_run.status == "canceling"
    assert child_run.status == "queued"


@pytest.mark.asyncio
async def test_terminal_usage_preserves_null_agent_id_after_purge(db: AsyncSession) -> None:
    root, _child, conversation = await _seed_execution_tree(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=root.id,
        context=unit_context(root.user_id),
        source="chat",
        input_preview="retained",
    )
    run.agent_id = None
    await db.flush()

    from app.agent_runtime.run_metrics_types import RunMetricsSnapshot
    from app.services.conversation_run_metrics_service import persist_run_metrics_and_usage

    snapshot = RunMetricsSnapshot(
        terminal_state="completed",
        elapsed_ms=1.0,
        ttft_ms=None,
        generation_ms=None,
        tokens_per_second=None,
        prompt_tokens=4,
        completion_tokens=2,
        cache_creation_tokens=None,
        cache_read_tokens=None,
        estimated_cost=None,
        usage_complete=True,
        root_tool_calls=0,
        descendant_tool_calls=0,
        root_subagent_calls=0,
        descendant_subagent_calls=0,
        activity=(),
        activity_truncated=False,
    )
    await persist_run_metrics_and_usage(db, run=run, snapshot=snapshot, model_name="retained")
    usage_agent_id = await db.scalar(select(TokenUsage.agent_id).where(TokenUsage.run_id == run.id))
    assert usage_agent_id is None
