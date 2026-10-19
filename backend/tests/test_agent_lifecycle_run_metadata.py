from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_api import AgentApiRun
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.services import agent_execution_owner, conversation_run_service
from app.services.agent_execution_owner import ExecutionEnvelopeRef, OwnedExecutionRequest
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, unit_context
from tests.conftest import TestSession, engine
from tests.test_agent_lifecycle_run_ownership import (
    _seed_execution_tree,
    _skip_sqlite_advisory_locks,
)


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
