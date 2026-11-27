"""An independent child's replay remains private even with a valid run or receipt ID."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState

from app.authz import runtime
from app.authz.client import AuthzUnavailable
from app.authz.fake import FakeAuthz
from app.authz.org_context import OrgContext
from app.authz.service import AuthzService
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.independent_chat_job import IndependentChatJob
from app.models.organization import OrganizationMember, TenantMember
from app.models.user import User
from app.services import independent_job_runs as reads
from tests.authz.test_independent_job_authority import delegation as delegation

pytestmark = pytest.mark.asyncio

type Delegation = tuple[OrgContext, ConversationRun, ConversationRun, IndependentChatJob]


def _child_run(
    db: AsyncSession,
    job: IndependentChatJob,
    *,
    source: str,
    parent_id: UUID | None,
    status: str = "completed",
) -> ConversationRun:
    run = ConversationRun(
        user_id=job.user_id,
        tenant_id=job.tenant_id,
        org_id=job.org_id,
        agent_id=job.child_agent_id,
        conversation_id=job.child_conversation_id,
        source=source,
        parent_run_id=parent_id,
        status=status,
        run_source="CHAT",
        invoker_type="user",
        is_active=False,
    )
    db.add(run)
    return run


async def test_initial_and_resumed_history_share_a_read_only_boundary(
    db: AsyncSession, delegation: Delegation
) -> None:
    context, parent, initial, job = delegation
    initial.status, initial.is_active = "interrupted", False
    first = _child_run(db, job, source="resume", parent_id=initial.id, status="interrupted")
    await db.flush()
    last = _child_run(db, job, source="resume", parent_id=first.id)
    ordinary = _child_run(db, job, source="chat", parent_id=None)
    await db.flush()
    detached = _child_run(db, job, source="resume", parent_id=ordinary.id)
    after_completion = _child_run(db, job, source="resume", parent_id=last.id)
    external = _child_run(db, job, source="resume", parent_id=first.id)
    external.run_source, external.invoker_type = "API", "api"
    await db.commit()
    conversation_id = job.child_conversation_id
    requested = [
        initial.id,
        first.id,
        last.id,
        parent.id,
        ordinary.id,
        detached.id,
        after_completion.id,
        external.id,
    ]
    unrelated = User(email="pending@test.invalid", name="Unflushed")
    db.add(unrelated)
    writes: list[str] = []

    def record_sql(state: ORMExecuteState) -> None:
        if not state.is_select or getattr(state.statement, "_for_update_arg", None) is not None:
            writes.append(str(state.statement))

    def record_commit(*_: object) -> None:
        writes.append("commit")

    def record_flush(*_: object) -> None:
        writes.append("flush")

    event.listen(db.sync_session, "before_flush", record_flush)
    event.listen(db.sync_session, "do_orm_execute", record_sql)
    event.listen(db.sync_session, "after_commit", record_commit)
    try:
        assert await reads.can_read_independent_child_conversation(
            db,
            context=context,
            conversation_id=conversation_id,
        )
        assert await reads.readable_independent_child_run_ids(
            db,
            context=context,
            conversation_id=conversation_id,
            run_ids=requested,
        ) == {initial.id, first.id, last.id}
        assert await reads.readable_independent_child_run_ids(
            db, context=context, conversation_id=conversation_id
        ) == {initial.id, first.id, last.id}
        assert not await reads.readable_independent_child_run_ids(
            db, context=context, conversation_id=conversation_id, run_ids=[]
        )
        for run_id in requested:
            assert await reads.can_read_independent_child_run(
                db,
                context=context,
                conversation_id=conversation_id,
                run_id=run_id,
            ) is (run_id in {initial.id, first.id, last.id})
        assert await reads.notification_jobs_visible(
            db, {job.id: (conversation_id, last.id)}, context
        ) == {job.id}
        for old in [initial, first, ordinary, detached, after_completion, external]:
            assert not await reads.notification_jobs_visible(
                db, {job.id: (conversation_id, old.id)}, context
            )
    finally:
        event.remove(db.sync_session, "before_flush", record_flush)
        event.remove(db.sync_session, "do_orm_execute", record_sql)
        event.remove(db.sync_session, "after_commit", record_commit)
    assert not writes
    assert list(db.new) == [unrelated]
    assert not (db.dirty or db.deleted)


async def test_canceled_child_is_still_readable_to_its_owner(
    db: AsyncSession, delegation: Delegation
) -> None:
    context, _, child, job = delegation
    child.status, child.is_active = "canceled", False
    job.cancel_requested_at = datetime.now(UTC)
    await db.commit()
    assert await reads.can_read_independent_child_run(
        db,
        context=context,
        conversation_id=job.child_conversation_id,
        run_id=child.id,
    )
    assert await reads.can_read_independent_child_conversation(
        db,
        context=context,
        conversation_id=job.child_conversation_id,
    )
    assert not await reads.notification_jobs_visible(
        db, {job.id: (job.child_conversation_id, child.id)}, context
    )


async def test_ids_do_not_authorize_ordinary_or_unrelated_conversations(
    db: AsyncSession, delegation: Delegation
) -> None:
    context, parent, child, job = delegation
    for conversation_id, run_id in [
        (parent.conversation_id, parent.id),
        (parent.conversation_id, child.id),
        (uuid4(), child.id),
        (job.child_conversation_id, uuid4()),
    ]:
        assert not await reads.can_read_independent_child_run(
            db,
            context=context,
            conversation_id=conversation_id,
            run_id=run_id,
        )
        assert not await reads.readable_independent_child_run_ids(
            db,
            context=context,
            conversation_id=conversation_id,
            run_ids=[run_id],
        )
    assert not await reads.can_read_independent_child_conversation(
        db,
        context=context,
        conversation_id=parent.conversation_id,
    )


@pytest.mark.parametrize(
    "loss",
    [
        "parent_deleted",
        "initial_provenance",
        "parent_provenance",
        "parent_archived",
        "child_archived",
        "broken_edge",
        "removed_edge",
        "org_member_removed",
        "tenant_member_removed",
        "other_user",
        "other_org",
        "other_tenant",
    ],
)
async def test_revocation_hides_chat_run_and_previously_verified_completion(
    db: AsyncSession,
    delegation: Delegation,
    loss: str,
) -> None:
    context, parent, child, job = delegation
    child.status, child.is_active = "completed", False
    await db.commit()
    conversation_id, run_id = job.child_conversation_id, child.id
    receipt = {job.id: (conversation_id, run_id)}
    assert await reads.notification_jobs_visible(db, receipt, context) == {job.id}
    if loss == "parent_deleted":
        await db.execute(delete(Conversation).where(Conversation.id == parent.conversation_id))
    elif loss == "initial_provenance":
        child.run_source = "API"
    elif loss == "parent_provenance":
        parent.run_source = "API"
    elif loss in {"parent_archived", "child_archived"}:
        agent = await db.get(
            Agent, job.parent_agent_id if loss == "parent_archived" else job.child_agent_id
        )
        assert agent is not None
        agent.archived_at = datetime.now(UTC)
    elif loss in {"broken_edge", "removed_edge"}:
        edge = await db.get(AgentSubAgentLink, (job.parent_agent_id, job.child_agent_id))
        assert edge is not None
        if loss == "removed_edge":
            await db.delete(edge)
        else:
            edge.link_status = "broken"
    elif loss == "org_member_removed":
        org_member = await db.get(OrganizationMember, (context.org_id, context.user_id))
        assert org_member is not None
        await db.delete(org_member)
    elif loss == "tenant_member_removed":
        tenant_member = await db.get(TenantMember, (context.tenant_id, context.user_id))
        assert tenant_member is not None
        await db.delete(tenant_member)
    elif loss == "other_user":
        context = replace(context, user_id=uuid4())
    elif loss == "other_org":
        context = replace(context, org_id=uuid4())
    else:
        context = replace(context, tenant_id=uuid4())
    await db.commit()

    assert not await reads.can_read_independent_child_run(
        db,
        context=context,
        conversation_id=conversation_id,
        run_id=run_id,
    )
    assert not await reads.can_read_independent_child_conversation(
        db,
        context=context,
        conversation_id=conversation_id,
    )
    assert not await reads.readable_independent_child_run_ids(
        db,
        context=context,
        conversation_id=conversation_id,
        run_ids=[run_id],
    )
    assert not await reads.notification_jobs_visible(db, receipt, context)


@pytest.mark.parametrize("grants", ["both", "only_parent", "only_child", "unavailable"])
async def test_enforced_visibility_rechecks_both_agent_grants_and_denies_on_outage(
    db: AsyncSession,
    delegation: Delegation,
    monkeypatch: pytest.MonkeyPatch,
    grants: str,
) -> None:
    context, _, child, job = delegation
    child.status, child.is_active = "completed", False
    await db.commit()
    actors = {job.parent_agent_id, job.child_agent_id}
    if grants == "only_parent":
        actors = {job.parent_agent_id}
    elif grants == "only_child":
        actors = {job.child_agent_id}
    client = FakeAuthz(
        frozenset((f"user:{context.user_id}", "can_run", f"agent:{a}") for a in actors)
    )
    enforcer = AuthzService(client, mode="enforce", enforce_types=frozenset({"agent"}))
    monkeypatch.setattr(runtime, "_current", enforcer)
    if grants == "unavailable":
        from unittest.mock import AsyncMock

        monkeypatch.setattr(
            FakeAuthz, "batch_check", AsyncMock(side_effect=AuthzUnavailable("batch"))
        )
    expected = grants == "both"
    assert (
        await reads.can_read_independent_child_run(
            db,
            context=context,
            conversation_id=job.child_conversation_id,
            run_id=child.id,
        )
        is expected
    )
    assert (
        await reads.can_read_independent_child_conversation(
            db,
            context=context,
            conversation_id=job.child_conversation_id,
        )
        is expected
    )
    assert await reads.notification_jobs_visible(
        db,
        {job.id: (job.child_conversation_id, child.id)},
        context,
    ) == ({job.id} if expected else set())


async def test_shared_fixed_parent_requires_current_delegation_on_child_history(
    db: AsyncSession, delegation: Delegation, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.dependencies import CurrentUser
    from app.schemas.independent_chat_job import IndependentJobCreate
    from app.services import independent_jobs
    from app.services.agent_execution_acceptance import (
        OwnedExecutionRequest,
        accept_owned_execution,
    )
    from tests.authz.unit_scope_fixture import ensure_unit_scope

    context, _, _, seed = delegation
    parent = await db.get(Agent, seed.parent_agent_id)
    child = await db.get(Agent, seed.child_agent_id)
    assert parent and child
    publisher = CurrentUser(id=uuid4(), email="delegate-publisher@test.invalid", name="Publisher")
    await ensure_unit_scope(db, publisher)
    parent.identity_mode = "fixed"
    child.user_id, child.identity_mode = publisher.id, "per_user"
    origin = Conversation(
        agent_id=parent.id,
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        agent_name_snapshot=parent.name,
        title="Fixed delegation",
        last_activity_at=datetime.now(UTC),
    )
    db.add(origin)
    await db.commit()

    parent_object, child_object = f"agent:{parent.id}", f"agent:{child.id}"
    caller_grants = frozenset(
        (f"user:{context.user_id}", "can_run", target) for target in (parent_object, child_object)
    )
    fixed_grant = (parent_object, "can_run", child_object)

    def set_grants(grants: frozenset[tuple[str, str, str]]) -> None:
        monkeypatch.setattr(
            runtime,
            "_current",
            AuthzService(FakeAuthz(grants), mode="enforce", enforce_types=frozenset({"agent"})),
        )

    set_grants(caller_grants | {fixed_grant})
    monkeypatch.setattr(independent_jobs, "independent_jobs_available", lambda: True)
    accepted_parent = await accept_owned_execution(
        db, OwnedExecutionRequest(origin.id, parent.id, context, "chat", None)
    )
    await db.commit()
    approved_run = await db.get(ConversationRun, accepted_parent.run_id)
    assert approved_run
    approved_run.status, approved_run.is_active = "completed", False
    await db.commit()
    accepted = await independent_jobs.accept_independent_job(
        db,
        origin,
        approved_run.id,
        IndependentJobCreate(
            request_id=uuid4(), target_id=child.id, instruction="Read the question"
        ),
        context,
    )
    await db.commit()
    job = accepted.job
    child_run = await db.get(ConversationRun, job.child_run_id)
    assert child_run
    child_run.status, child_run.is_active = "completed", False
    await db.commit()

    for grants, expected in [(caller_grants | {fixed_grant}, True), (caller_grants, False)]:
        set_grants(grants)
        assert (
            await reads.can_read_independent_child_run(
                db,
                context=context,
                conversation_id=job.child_conversation_id,
                run_id=job.child_run_id,
            )
            is expected
        )
        assert await reads.notification_jobs_visible(
            db, {job.id: (job.child_conversation_id, job.child_run_id)}, context
        ) == ({job.id} if expected else set())

    # Ownership of the published child and even full agent grants do not give
    # its author access to the private history of the user who submitted it.
    other_context = replace(context, user_id=publisher.id)
    publisher_grants = frozenset(
        (f"user:{publisher.id}", "can_run", target) for target in (parent_object, child_object)
    )
    set_grants(publisher_grants | {fixed_grant})
    assert not await reads.can_read_independent_child_run(
        db,
        context=other_context,
        conversation_id=job.child_conversation_id,
        run_id=job.child_run_id,
    )
    assert not await reads.notification_jobs_visible(
        db, {job.id: (job.child_conversation_id, job.child_run_id)}, other_context
    )
