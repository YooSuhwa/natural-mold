"""Detached queue authorization precedes private hydration, including cleanup."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never
from uuid import UUID

import anyio
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.organization import OrganizationMember, Tenant, TenantMember
from app.models.user import User
from app.services import conversation_run_queue_service, conversation_run_queue_worker
from app.services import conversation_run_worker as runtime
from tests.authz.org_context_fixture import org_fixture as org_fixture
from tests.authz.private_scope_fixture import private_scopes as private_scopes
from tests.authz.queue_resource_fixture import queue_resource_case as queue_resource_case
from tests.authz.queue_resource_fixture import queue_resource_sql, snapshot_bytes

if TYPE_CHECKING:
    from tests.authz.queue_resource_fixture import QueueResourceCase

type WorkerDenial = Literal[
    "membership", "tenant_member", "tenant", "inactive", "moved_agent", "child_agent"
]


def private_selects(statements: list[str]) -> list[str]:
    fields = (
        "conversation_run_inputs.input_payload",
        "conversation_run_inputs.attachment_ids",
        "conversation_run_inputs.checkpoint_id",
        "conversations.title",
        "conversations.runtime_policy_snapshot",
        "conversations.last_activity_at",
        "conversation_runs.input_preview",
        "conversation_runs.metadata_json",
    )
    return [
        sql
        for sql in statements
        if sql.lstrip().lower().startswith("select")
        and any(field in sql.lower() for field in fields)
    ]


async def claim_case(db: AsyncSession, case: QueueResourceCase) -> ConversationRun:
    claimed = await conversation_run_queue_service.claim_next_input(
        db, conversation_id=case.queued.conversation_id, context=case.scope.context
    )
    assert claimed is not None
    await db.commit()
    return claimed.run


async def revoke_case(db: AsyncSession, case: QueueResourceCase, denial: WorkerDenial) -> None:
    own = case.scope.context
    match denial:
        case "membership":
            stmt = (
                update(OrganizationMember)
                .where(
                    OrganizationMember.org_id == own.org_id,
                    OrganizationMember.user_id == own.user_id,
                )
                .values(status="suspended")
            )
        case "tenant_member":
            stmt = (
                update(TenantMember)
                .where(TenantMember.tenant_id == own.tenant_id, TenantMember.user_id == own.user_id)
                .values(status="suspended")
            )
        case "tenant":
            stmt = update(Tenant).where(Tenant.id == own.tenant_id).values(status="suspended")
        case "inactive":
            stmt = update(User).where(User.id == own.user_id).values(is_active=False)
        case "moved_agent":
            other = case.scopes.second
            stmt = (
                update(Agent)
                .where(Agent.id == case.scope.agent_id)
                .values(org_id=other.org_id, tenant_id=other.tenant_id)
            )
        case "child_agent":
            model_id = await db.scalar(
                select(Agent.model_id).where(Agent.id == case.scope.agent_id)
            )
            other = Agent(
                user_id=own.user_id,
                org_id=own.org_id,
                tenant_id=own.tenant_id,
                name="Other queue agent",
                system_prompt="Fixture",
                model_id=model_id,
            )
            db.add(other)
            await db.flush()
            stmt = (
                update(ConversationRunInput)
                .where(ConversationRunInput.id == case.queued.id)
                .values(agent_id=other.id)
            )
        case unreachable:
            assert_never(unreachable)
    await db.execute(stmt.execution_options(synchronize_session=False))
    await db.commit()


@pytest.mark.parametrize(
    "variant",
    [
        (denial, operator)
        for denial in (
            "membership",
            "tenant_member",
            "tenant",
            "inactive",
            "moved_agent",
            "child_agent",
        )
        for operator in (False, True)
    ],
)
async def test_denied_launch_and_cleanup_read_no_private_content(
    db: AsyncSession,
    queue_resource_case: QueueResourceCase,
    variant: tuple[WorkerDenial, bool],
) -> None:
    # Given a committed claim whose current actor or exact execution binding is invalid.
    case = queue_resource_case
    denial, operator = variant
    run = await claim_case(db, case)
    await db.execute(
        update(User).where(User.id == case.scope.context.user_id).values(is_super_user=operator)
    )
    await db.commit()
    await revoke_case(db, case, denial)
    revision = case.queued.revision

    # When the actual detached consumer attempts launch and terminal cleanup.
    with queue_resource_sql(db) as statements:
        result = await conversation_run_queue_worker.launch_claimed_input(case.queued.id)

    # Then failure changes only lifecycle fields without hydrating protected content.
    assert private_selects(statements) == []
    await db.refresh(case.queued)
    await db.refresh(run)
    assert case.queued.status == "failed" and case.queued.revision == revision + 1
    assert snapshot_bytes(case.queued.input_payload) == case.snapshot
    assert case.queued.priority == 0
    if denial == "child_agent":
        assert result is None and run.status == "queued"
    else:
        assert run.status == "failed" and run.is_active is False
        assert run.error_code == (
            "queue_user_unavailable"
            if denial == "inactive"
            else "queue_binding_invalid"
            if denial == "moved_agent"
            else "queue_rehydrate_failed"
        )


@pytest.mark.parametrize(
    "control",
    [(False, False), (True, False), (True, True)],
    ids=["normal-control", "read-order", "revoked-live"],
)
async def test_live_registry_requires_actor_before_return_and_skips_payload(
    db: AsyncSession,
    queue_resource_case: QueueResourceCase,
    control: tuple[bool, bool],
) -> None:
    # Given an actual live task already registered for this committed claim.
    case = queue_resource_case
    read_order, revoked = control
    run = await claim_case(db, case)
    waiter = anyio.Event()
    task = runtime.asyncio.create_task(waiter.wait())
    registry = runtime.get_run_task_registry()
    registry.start(run.id, task)
    revision = case.queued.revision
    try:
        if revoked:
            await revoke_case(db, case, "membership")
        # When duplicate detached launch encounters the live local task.
        with queue_resource_sql(db) as statements:
            result = await conversation_run_queue_worker.launch_claimed_input(case.queued.id)
    finally:
        waiter.set()
        await task
        registry.discard(run.id)

    # Then current actor SQL precedes the return, with no payload/runtime hydration or writes.
    assert result is not None and result.id == run.id
    if read_order:
        assert any("organization_members" in sql.lower() for sql in statements)
        assert private_selects(statements) == []
    if not revoked:
        assert not any(
            sql.lstrip().lower().startswith(("update", "insert", "delete")) for sql in statements
        )
    await db.refresh(case.queued)
    assert case.queued.status == ("failed" if revoked else "claimed")
    assert case.queued.revision == revision + int(revoked)
    assert snapshot_bytes(case.queued.input_payload) == case.snapshot


async def test_cleanup_after_revocation_uses_only_bound_terminal_metadata(
    db: AsyncSession,
    queue_resource_case: QueueResourceCase,
) -> None:
    # Given a legitimately bound queued run after organization membership loss.
    case = queue_resource_case
    run = await claim_case(db, case)
    await revoke_case(db, case, "membership")
    revision = case.queued.revision

    # When membership-independent failure cleanup runs.
    with queue_resource_sql(db) as statements:
        await conversation_run_queue_worker._fail_claimed_input(
            case.queued.id, error_code="queue_rehydrate_failed"
        )

    # Then only lifecycle metadata is read and the exact input/run terminalize once.
    assert private_selects(statements) == []
    assert not any("organization_members" in sql.lower() for sql in statements)
    await db.refresh(run)
    await db.refresh(case.queued)
    assert run.status == "failed" and run.error_code == "queue_rehydrate_failed"
    assert case.queued.status == "failed" and case.queued.revision == revision + 1


async def test_recovery_claim_collector_reads_ids_without_payload(
    db: AsyncSession,
    queue_resource_case: QueueResourceCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a committed claim and a recording launch boundary.
    case = queue_resource_case
    run = await claim_case(db, case)
    launched: list[ConversationRunInput] = []

    async def record_launch(input_id: UUID) -> ConversationRun:
        assert input_id == case.queued.id
        launched.append(case.queued)
        return run

    monkeypatch.setattr(conversation_run_queue_worker, "launch_claimed_input", record_launch)

    # When recovery enumerates claim-before-launch gaps.
    with queue_resource_sql(db) as statements:
        recovered = await conversation_run_queue_worker.recover_conversation_queue()

    # Then one exact claim is forwarded using an ID-only Input projection.
    assert recovered == 1 and launched == [case.queued]
    collector = [
        sql
        for sql in statements
        if sql.lstrip().lower().startswith("select")
        and "from conversation_run_inputs" in sql.lower()
        and "join conversation_runs" in sql.lower()
    ]
    assert len(collector) == 1
    assert private_selects(collector) == []
