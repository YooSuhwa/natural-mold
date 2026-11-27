"""A committed private dispatch remains runnable only under its original current authority."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import runtime as authz_runtime
from app.authz.conversation_agent_checks import require_agent_run
from app.authz.independent_job_execution import authorize_independent_job_execution
from app.authz.org_context import OrgContext
from app.authz.run_decisions import fresh_run_decision_scope
from app.config import settings
from app.models.ag_ui_command_receipt import AgUiCommandReceipt
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation_run import ConversationRun, utc_now_naive
from app.models.conversation_run_input import ConversationRunInput
from app.models.independent_chat_job import IndependentChatJob
from app.models.organization import OrganizationMember, TenantMember
from app.services import ag_ui_deferred_delivery as delivery
from app.services import conversation_run_queue_worker as queue
from app.services import conversation_run_service as runs
from app.services import conversation_run_worker as worker
from app.services.agent_execution_owner import OwnedExecutionRequest, accept_owned_execution
from tests.ag_ui_deferred_testkit import seed_deferred, stage_receipt
from tests.authz.org_context_fixture import org_fixture as org_fixture
from tests.authz.private_scope_fixture import PrivateScopes
from tests.authz.private_scope_fixture import private_scopes as private_scopes
from tests.authz.queue_resource_fixture import queue_resource_sql
from tests.authz.test_conversation_route_authz import _service
from tests.authz.test_independent_job_authority import delegation as delegation
from tests.conftest import TestSession, make_user


def assert_no_projection(statements: list[str], columns: tuple[str, ...]) -> None:
    projected = [
        " ".join(sql.lower().split()).partition(" from ")[0]
        for sql in statements
        if sql.lstrip().lower().startswith("select")
    ]
    assert not any(column in cols for cols in projected for column in columns)


@pytest.fixture
def saver(monkeypatch: pytest.MonkeyPatch) -> InMemorySaver:
    memory = InMemorySaver()
    monkeypatch.setattr("app.agent_runtime.checkpointer._checkpointer", memory)
    monkeypatch.setattr(queue, "async_session", TestSession)
    monkeypatch.setattr(worker, "async_session", TestSession)
    monkeypatch.setattr(settings, "e2e_scripted_model_enabled", True)
    return memory


async def test_stale_sweep_preserves_only_recoverable_workerless_commands(
    db: AsyncSession, saver: InMemorySaver, private_scopes: PrivateScopes
) -> None:
    recoverable = await seed_deferred(db, private_scopes.first, saver=saver)
    await db.commit()
    ordinary = await seed_deferred(db, private_scopes.second, saver=saver)
    await db.delete(ordinary.receipt)  # a legacy workerless run has no durable dispatch input
    cutoff = utc_now_naive() - timedelta(seconds=settings.chat_run_stale_after_seconds)
    await db.execute(update(ConversationRun).values(created_at=cutoff - timedelta(days=1)))
    await db.commit()

    # Startup performs this sweep before attempting durable dispatch.
    await runs.sweep_stale_conversation_runs(session_factory=TestSession)
    db.expire_all()
    retained = await db.get(ConversationRun, recoverable.run_id)
    expired = await db.get(ConversationRun, ordinary.run_id)
    assert retained is not None and retained.status == "queued" and retained.is_active
    assert expired is not None and expired.status == "stale" and not expired.is_active
    assert await db.scalar(select(func.count()).select_from(ConversationRunInput)) == 0


@pytest.mark.parametrize("denial", ["membership", "revoked_grant"])
async def test_revocation_after_acceptance_precedes_private_delivery_read(
    db: AsyncSession,
    saver: InMemorySaver,
    private_scopes: PrivateScopes,
    monkeypatch: pytest.MonkeyPatch,
    denial: str,
) -> None:
    ctx = private_scopes.first
    case = await seed_deferred(db, ctx, saver=saver)
    await db.commit()
    grant = (f"user:{ctx.user_id}", "can_run", f"agent:{case.agent_id}")
    monkeypatch.setattr(authz_runtime, "_current", _service("enforce", {grant}))
    launch = AsyncMock()
    monkeypatch.setattr(queue, "start_conversation_run", launch)
    with fresh_run_decision_scope() as accepted:
        await require_agent_run(ctx, case.agent_id)
        assert accepted.answers
        if denial == "membership":
            await db.execute(
                update(OrganizationMember)
                .where(
                    OrganizationMember.org_id == ctx.org_id,
                    OrganizationMember.user_id == ctx.user_id,
                )
                .values(status="suspended")
            )
            await db.commit()
        else:
            monkeypatch.setattr(authz_runtime, "_current", _service("enforce"))
        with queue_resource_sql(db) as statements:
            await delivery.dispatch_ag_ui_run(case.run_id)
    launch.assert_not_awaited()
    protected = (
        "ag_ui_command_receipts.delivery_encrypted",
        "conversation_runs.input_preview",
        "conversation_runs.metadata_json",
    )
    assert_no_projection(statements, protected)
    db.expire_all()
    run = await db.get(ConversationRun, case.run_id)
    assert run is not None and not run.is_active and run.worker_instance_id is None


@pytest.mark.parametrize("other_actor", [False, True], ids=["other-company", "other-user"])
async def test_copied_ciphertext_cannot_dispatch_or_mutate_its_original_run(
    db: AsyncSession,
    saver: InMemorySaver,
    private_scopes: PrivateScopes,
    monkeypatch: pytest.MonkeyPatch,
    other_actor: bool,
) -> None:
    ctx = private_scopes.first
    if other_actor:
        user = await make_user(db)
        ctx = replace(private_scopes.second, user_id=user.id)
        db.add(TenantMember(tenant_id=ctx.tenant_id, user_id=user.id))
        db.add(OrganizationMember(tenant_id=ctx.tenant_id, org_id=ctx.org_id, user_id=user.id))
        await db.flush()
    original = await seed_deferred(db, ctx, saver=saver)
    await db.commit()
    target = await seed_deferred(db, private_scopes.second, saver=saver)
    target.receipt.delivery_encrypted = original.receipt.delivery_encrypted
    await db.commit()
    launch = AsyncMock()
    monkeypatch.setattr(queue, "start_conversation_run", launch)

    await delivery.dispatch_ag_ui_run(target.run_id)
    launch.assert_not_awaited()
    db.expire_all()
    source_run = await db.get(ConversationRun, original.run_id)
    invalid = await db.get(ConversationRun, target.run_id)
    assert source_run is not None and source_run.status == "queued" and source_run.is_active
    assert invalid is not None and invalid.status == "failed" and not invalid.is_active


async def test_recovery_skips_rolled_back_and_canceled_unlaunched_dispatches(
    db: AsyncSession,
    saver: InMemorySaver,
    private_scopes: PrivateScopes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rolled_back = await seed_deferred(db, private_scopes.first, saver=saver)
    await db.rollback()
    assert await db.get(ConversationRun, rolled_back.run_id) is None
    canceled = await seed_deferred(db, private_scopes.second, saver=saver)
    await db.commit()
    run = await db.get(ConversationRun, canceled.run_id)
    assert run is not None
    await runs.request_cancel_run(db, run, reason="stop")
    await db.commit()
    launch = AsyncMock()
    monkeypatch.setattr(queue, "start_conversation_run", launch)
    # The existing workerless-cancel handler acknowledges the stop before recovery scans.
    assert await queue.recover_conversation_queue() == 0
    launch.assert_not_awaited()
    db.expire_all()
    run = await db.get(ConversationRun, canceled.run_id)
    assert run is not None and run.status == "canceled" and run.cancellation_acknowledged_at
    assert await db.scalar(select(func.count()).select_from(AgUiCommandReceipt)) == 1


async def test_incompatible_worker_retains_the_committed_command_for_a_matching_binary(
    db: AsyncSession,
    saver: InMemorySaver,
    private_scopes: PrivateScopes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import ag_ui_commands

    case = await seed_deferred(db, private_scopes.first, saver=saver)
    aged = utc_now_naive() - timedelta(seconds=settings.chat_run_stale_after_seconds + 60)
    await db.execute(
        update(ConversationRun).where(ConversationRun.id == case.run_id).values(created_at=aged)
    )
    await db.commit()
    queue_launch = AsyncMock()
    private_launch = AsyncMock()
    monkeypatch.setattr(queue, "launch_claimed_input", queue_launch)
    monkeypatch.setattr(queue, "start_conversation_run", private_launch)
    with monkeypatch.context() as incompatible:
        incompatible.setattr(ag_ui_commands, "PROJECTION_VERSION", "another-version")
        await runs.sweep_stale_conversation_runs(session_factory=TestSession)
        assert await queue.recover_conversation_queue() == 0
    queue_launch.assert_not_awaited()
    private_launch.assert_not_awaited()
    async with TestSession() as verification:
        retained = await verification.get(ConversationRun, case.run_id)
        assert retained is not None and retained.status == "queued" and retained.is_active
        receipt = await verification.get(
            AgUiCommandReceipt, (case.conversation_id, case.public_run_id)
        )
        assert receipt is not None and receipt.projection_version == "1"
    assert await queue.recover_conversation_queue() == 1
    assert private_launch.await_args is not None
    assert private_launch.await_args.kwargs["run_id"] == case.run_id
    queue_launch.assert_not_awaited()


@pytest.mark.parametrize("revocation", ["cancel-job", "remove-delegation"])
async def test_independent_resume_rechecks_delegation_before_opening_the_private_response(
    db: AsyncSession,
    saver: InMemorySaver,
    delegation: tuple[OrgContext, ConversationRun, ConversationRun, IndependentChatJob],
    monkeypatch: pytest.MonkeyPatch,
    revocation: str,
) -> None:
    ctx, _, initial, job = delegation
    await runs.transition_run(db, initial, "running", worker_instance_id="delegated")
    await runs.transition_run(db, initial, "interrupted", interrupt_id="review")
    await db.commit()
    accepted = await accept_owned_execution(
        db,
        OwnedExecutionRequest(
            job.child_conversation_id,
            job.child_agent_id,
            ctx,
            "chat",
            None,
            run_source="resume",
            parent_run_id=initial.id,
            interrupt_id="review",
        ),
    )
    resumed = await db.get(ConversationRun, accepted.run_id)
    assert resumed is not None
    await stage_receipt(
        db,
        ctx,
        resumed,
        "accepted-branch-point",
        {
            "interruptIds": ["review"],
            "response": {"review": {"decisions": [{"type": "approve"}]}},
            "approvalRequestIds": {},
        },
    )
    await db.commit()
    assert await authorize_independent_job_execution(
        db, conversation_id=job.child_conversation_id, run_id=accepted.run_id, context=ctx
    )
    await db.commit()
    if revocation == "cancel-job":
        job.cancel_requested_at = datetime.now(UTC)
    else:
        link = await db.get(AgentSubAgentLink, (job.parent_agent_id, job.child_agent_id))
        assert link is not None
        await db.delete(link)
    await db.commit()
    launch = AsyncMock()
    monkeypatch.setattr(queue, "start_conversation_run", launch)
    with queue_resource_sql(db) as statements:
        await delivery.dispatch_ag_ui_run(accepted.run_id)
    launch.assert_not_awaited()
    assert_no_projection(statements, ("ag_ui_command_receipts.delivery_encrypted",))
    await db.refresh(resumed)
    assert resumed.status == "failed" and not resumed.is_active
