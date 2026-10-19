"""The frozen authority-denial projection shared by archive and tenant suspension."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.prepared_terminal_writer import terminalize_authority_denial
from app.models.agent_api import AgentApiRun
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from tests.authz.execution_gateway_testkit import seed_graph


def _input(graph, conversation_id, position: int, run_id=None) -> ConversationRunInput:
    return ConversationRunInput(
        conversation_id=conversation_id,
        agent_id=graph.agent_id,
        user_id=graph.context.user_id,
        run_id=run_id,
        client_request_id=f"req-{position}",
        source="chat",
        status="claimed" if run_id else "pending",
        position=position,
        revision=1,
        input_payload={"message": "x"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["organization_archived", "tenant_suspended"])
async def test_denial_projects_exact_terminal_fields(db: AsyncSession, reason: str) -> None:
    graph = await seed_graph(db, conversations=2)
    first, second = graph.conversation_ids
    run = ConversationRun(
        conversation_id=first,
        agent_id=graph.agent_id,
        user_id=graph.context.user_id,
        source="chat",
        status="queued",
        is_active=True,
    )
    leased = ConversationRun(
        conversation_id=second,
        agent_id=graph.agent_id,
        user_id=graph.context.user_id,
        source="chat",
        status="running",
        is_active=True,
        runtime_materialized_at=datetime.now(UTC).replace(tzinfo=None),
        runtime_materialization_sha256="a" * 64,
    )
    db.add_all((run, leased))
    await db.flush()
    pending = _input(graph, first, 1)
    claimed = _input(graph, first, 2, run.id)
    api_run = AgentApiRun(
        public_id=f"run_{uuid.uuid4().hex}",
        user_id=graph.context.user_id,
        deployment_public_id_snapshot="dep",
        conversation_run_id=run.id,
        conversation_id=first,
        mode="wait",
        status="running",
    )
    trigger_run = AgentTriggerRun(
        trigger_name_snapshot="t",
        conversation_run_id=run.id,
        user_id=graph.context.user_id,
        conversation_id=first,
        status="running",
        input_message="hi",
    )
    db.add_all((pending, claimed, api_run, trigger_run))
    await db.flush()

    outcome = terminalize_authority_denial(
        reason,  # type: ignore[arg-type]
        inputs=(pending, claimed),
        runs=(run, leased),
        api_runs=(api_run,),
        trigger_runs=(trigger_run,),
    )
    await db.flush()

    assert (pending.status, pending.run_id, pending.revision) == ("canceled", None, 2)
    assert (claimed.status, claimed.run_id) == ("failed", run.id)
    assert (run.status, run.is_active, run.error_code) == ("failed", False, reason)
    assert run.completed_at is not None and run.error_message
    assert (leased.status, leased.is_active, leased.error_code) == ("running", True, None)
    assert (api_run.status, api_run.error_code, api_run.conversation_run_id) == (
        "failed",
        reason,
        run.id,
    )
    assert api_run.finished_at is not None and api_run.error_message == run.error_message
    assert (trigger_run.status, trigger_run.error_message) == ("failed", reason)
    assert trigger_run.finished_at is not None and trigger_run.conversation_run_id == run.id
    assert (outcome.canceled_inputs, outcome.failed_inputs, outcome.failed_runs) == (1, 1, 1)
    assert (outcome.lease_owned_runs, outcome.failed_api_runs, outcome.failed_trigger_runs) == (
        1,
        1,
        1,
    )


@pytest.mark.asyncio
async def test_lease_owned_terminal_and_unlinked_rows_are_left_alone_and_idempotent(
    db: AsyncSession,
) -> None:
    graph = await seed_graph(db, conversations=2)
    first, second = graph.conversation_ids
    leased = ConversationRun(
        conversation_id=first,
        agent_id=graph.agent_id,
        user_id=graph.context.user_id,
        source="chat",
        status="running",
        is_active=True,
        runtime_materialized_at=datetime.now(UTC).replace(tzinfo=None),
        runtime_materialization_sha256="b" * 64,
    )
    done = ConversationRun(
        conversation_id=second,
        agent_id=graph.agent_id,
        user_id=graph.context.user_id,
        source="chat",
        status="completed",
        is_active=False,
    )
    db.add_all((leased, done))
    await db.flush()
    leased_input = _input(graph, first, 1, leased.id)
    done_input = _input(graph, second, 2, done.id)
    pending = _input(graph, second, 3)
    unlinked = AgentTriggerRun(
        trigger_name_snapshot="t",
        user_id=graph.context.user_id,
        status="running",
        input_message="hi",
    )
    leased_api = AgentApiRun(
        public_id=f"run_{uuid.uuid4().hex}",
        user_id=graph.context.user_id,
        deployment_public_id_snapshot="dep",
        conversation_run_id=leased.id,
        mode="wait",
        status="running",
    )
    db.add_all((leased_input, done_input, pending, unlinked, leased_api))
    await db.flush()
    rows = {
        "inputs": (leased_input, done_input, pending),
        "runs": (leased, done),
        "api_runs": (leased_api,),
        "trigger_runs": (unlinked,),
    }

    first_pass = terminalize_authority_denial("organization_archived", **rows)
    second_pass = terminalize_authority_denial("organization_archived", **rows)

    assert (leased_input.status, done_input.status) == ("claimed", "claimed")
    assert (pending.status, pending.revision) == ("canceled", 2)
    assert (leased.status, done.status) == ("running", "completed")
    assert (unlinked.status, leased_api.status) == ("running", "running")
    assert (first_pass.canceled_inputs, first_pass.lease_owned_runs) == (1, 1)
    assert second_pass.canceled_inputs == 0 and second_pass.failed_runs == 0


def test_seam_has_no_session_and_no_administrative_or_replay_surface() -> None:
    import inspect

    from app.agent_runtime import prepared_terminal_writer as writer

    parameters = inspect.signature(writer.terminalize_authority_denial).parameters
    assert set(parameters) == {"reason", "inputs", "runs", "api_runs", "trigger_runs"}
    public = {name for name in vars(writer) if not name.startswith("_")}
    assert not any("replay" in name or "enable" in name for name in public)
    assert not {"AgentTrigger", "AgentDeployment", "AsyncSession"} & public
