"""Exact archive terminal metadata, idempotence and live-lease exemption (internal I22)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.prepared_run_registry import (
    LeaseState,
    PreparedRunRegistry,
    get_prepared_run_registry,
    reset_prepared_run_registry_for_tests,
)
from app.authz.tenant_org_write_locks import lock_tenant_org_write
from app.models.agent_api import AgentApiRun, AgentDeployment
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.services.tenants.organization_archive_terminalizer import cascade_organization_archive
from tests.authz import prepared_run_testkit as kit
from tests.authz.execution_gateway_testkit import GatewayGraph, seed_graph
from tests.authz.organization_archive_workload import Workload, seed_workload

ARCHIVED_AT = datetime(2026, 10, 16, 12, 0, 0)


@pytest.fixture(autouse=True)
def _fresh_registry() -> Iterator[None]:
    reset_prepared_run_registry_for_tests(PreparedRunRegistry())
    yield
    reset_prepared_run_registry_for_tests()


async def _seeded(db: AsyncSession) -> tuple[GatewayGraph, Workload]:
    graph = await seed_graph(db, conversations=0)
    ctx = graph.context
    workload = await seed_workload(
        db, tenant_id=ctx.tenant_id, org_id=ctx.org_id, user_id=ctx.user_id
    )
    return graph, workload


async def _cascade(db: AsyncSession, graph: GatewayGraph):
    locked = await lock_tenant_org_write(db, graph.context.tenant_id, (graph.context.org_id,))
    outcome = await cascade_organization_archive(db, locked.organizations[0], ARCHIVED_AT)
    await db.commit()
    db.expire_all()
    return outcome


async def _get[T](db: AsyncSession, model: type[T], row_id: object) -> T:
    row = await db.get(model, row_id)
    assert row is not None
    return row


async def test_archive_terminal_metadata_is_exact_per_row_class(db: AsyncSession) -> None:
    graph, w = await _seeded(db)

    outcome = await _cascade(db, graph)

    pending = await _get(db, ConversationRunInput, w.pending_input_id)
    assert (pending.status, pending.run_id, pending.revision) == ("canceled", None, 2)
    for run_id, input_id in (
        (w.api_conversation_run_id, w.api_input_id),
        (w.trigger_conversation_run_id, w.trigger_input_id),
    ):
        run = await _get(db, ConversationRun, run_id)
        assert (run.status, run.is_active, run.error_code) == (
            "failed",
            False,
            "organization_archived",
        )
        assert run.completed_at is not None and run.metrics is None
        claimed = await _get(db, ConversationRunInput, input_id)
        assert (claimed.status, claimed.run_id, claimed.revision) == ("failed", run_id, 1)
    api_run = await _get(db, AgentApiRun, w.api_run_id)
    assert (api_run.status, api_run.error_code, api_run.conversation_run_id) == (
        "failed",
        "organization_archived",
        w.api_conversation_run_id,
    )
    assert api_run.finished_at is not None
    trigger_run = await _get(db, AgentTriggerRun, w.trigger_run_id)
    assert (trigger_run.status, trigger_run.error_message, trigger_run.conversation_run_id) == (
        "failed",
        "organization_archived",
        w.trigger_conversation_run_id,
    )
    assert trigger_run.finished_at is not None
    trigger = await _get(db, AgentTrigger, w.trigger_id)
    assert (trigger.status, trigger.next_run_at) == ("paused", None)
    deployment = await _get(db, AgentDeployment, w.deployment_id)
    assert (deployment.status, deployment.updated_at) == ("disabled", ARCHIVED_AT)
    assert outcome.paused_trigger_ids == (w.trigger_id,)
    assert outcome.disabled_deployment_ids == (w.deployment_id,)
    assert (
        outcome.denial.canceled_inputs,
        outcome.denial.failed_inputs,
        outcome.denial.failed_runs,
        outcome.denial.lease_owned_runs,
        outcome.denial.failed_api_runs,
        outcome.denial.failed_trigger_runs,
    ) == (1, 2, 2, 1, 1, 1)
    for model, row_id, code_field in (
        (AgentApiRun, w.unlinked_api_run_id, "error_code"),
        (AgentTriggerRun, w.unlinked_trigger_run_id, "error_message"),
    ):
        unlinked = await _get(db, model, row_id)
        assert (unlinked.status, getattr(unlinked, code_field)) == (
            "failed",
            "organization_archived",
        )
        assert unlinked.finished_at is not None and unlinked.conversation_run_id is None
    assert outcome.failed_unlinked_envelope_ids == (
        w.unlinked_api_run_id,
        w.unlinked_trigger_run_id,
    )


async def test_consumed_claimed_input_is_neither_enumerated_nor_locked(db: AsyncSession) -> None:
    graph, w = await _seeded(db)

    outcome = await _cascade(db, graph)

    locked = {resource for _class, resource in outcome.lock_trace}
    assert w.done_conversation_id not in locked
    assert w.done_input_id not in locked and w.done_run_id not in locked
    done_input = await _get(db, ConversationRunInput, w.done_input_id)
    assert (done_input.status, done_input.run_id, done_input.revision) == (
        "claimed",
        w.done_run_id,
        1,
    )
    done_run = await _get(db, ConversationRun, w.done_run_id)
    assert (done_run.status, done_run.error_code) == ("completed", None)


async def test_cascade_rerun_is_idempotent(db: AsyncSession) -> None:
    graph, w = await _seeded(db)
    await _cascade(db, graph)
    before = (await _get(db, ConversationRunInput, w.pending_input_id)).revision
    run_done = (await _get(db, ConversationRun, w.api_conversation_run_id)).completed_at

    again = await _cascade(db, graph)

    assert again.paused_trigger_ids == () and again.disabled_deployment_ids == ()
    assert (again.denial.canceled_inputs, again.denial.failed_runs) == (0, 0)
    assert (again.denial.failed_api_runs, again.denial.failed_trigger_runs) == (0, 0)
    assert again.failed_unlinked_envelope_ids == ()
    assert (await _get(db, ConversationRunInput, w.pending_input_id)).revision == before
    assert (await _get(db, ConversationRun, w.api_conversation_run_id)).completed_at == run_done


@pytest.mark.parametrize("live_lease", [True, False])
async def test_marker_run_is_left_to_its_lease_or_the_stale_sweep(
    db: AsyncSession, live_lease: bool
) -> None:
    """The marker pair is the cross-process exemption; the registry is process-local.

    With a live lease the run finishes through I14; without one (worker loss) the
    archive still leaves it, and the heartbeat stale sweep terminalizes it.
    """
    graph, w = await _seeded(db)
    registry = get_prepared_run_registry()
    probe = kit.CloseProbe()
    if live_lease:
        ctx = graph.context
        binding = replace(
            kit.binding(w.lease_run_id),
            tenant_id=ctx.tenant_id,
            org_id=ctx.org_id,
            user_id=ctx.user_id,
        )
        token = registry.reserve(binding)
        lease = kit.lease(binding, token, probe)
        await registry.stage(lease)
        registry.activate(binding.run_id, token=token, digest=lease.digest)

    outcome = await _cascade(db, graph)

    assert outcome.denial.lease_owned_runs == 1
    run = await _get(db, ConversationRun, w.lease_run_id)
    assert (run.status, run.is_active, run.error_code, run.completed_at) == (
        "running",
        True,
        None,
        None,
    )
    lease_input = await _get(db, ConversationRunInput, w.lease_input_id)
    assert (lease_input.status, lease_input.run_id) == ("claimed", w.lease_run_id)
    envelope = await _get(db, AgentApiRun, w.lease_api_run_id)
    assert (envelope.status, envelope.error_code, envelope.finished_at) == ("running", None, None)
    expected = LeaseState.ACTIVE if live_lease else None
    assert registry.state(w.lease_run_id) is expected
    assert probe.calls == 0


async def test_cascade_never_touches_another_organization(db: AsyncSession) -> None:
    graph, _ = await _seeded(db)
    other = await seed_graph(db, conversations=0)
    octx = other.context
    foreign = await seed_workload(
        db, tenant_id=octx.tenant_id, org_id=octx.org_id, user_id=octx.user_id
    )

    await _cascade(db, graph)

    assert (await _get(db, AgentTrigger, foreign.trigger_id)).status == "active"
    assert (await _get(db, AgentDeployment, foreign.deployment_id)).status == "active"
    pending = await _get(db, ConversationRunInput, foreign.pending_input_id)
    assert (pending.status, pending.revision) == ("pending", 1)
    run = await _get(db, ConversationRun, foreign.api_conversation_run_id)
    assert (run.status, run.is_active) == ("queued", True)
