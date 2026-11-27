"""Each child control boundary rechecks the accepted participant and live delegation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import child_task_authorization as authorization
from app.authz.client import Consistency
from app.authz.execution_lock_cursor import LockClass
from app.authz.organization_execution_proof import live_proof
from app.authz.principal import Principal
from app.config import settings
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation_run import ConversationRun
from app.models.organization import Organization, OrganizationMember, TenantMember
from app.models.resource_policy import ResourcePolicy
from app.models.user import User
from app.services.agent_execution_acceptance import OwnedExecutionRequest, accept_owned_execution
from app.services.child_task_errors import ChildTaskStopped
from tests.authz.authz_service_testkit import RecordingClient, service
from tests.authz.execution_gateway_testkit import GatewayGraph, seed_graph


@dataclass(frozen=True)
class ChildCase:
    graph: GatewayGraph
    run_id: UUID
    child_id: UUID


@pytest.fixture
async def child_case(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> ChildCase:
    monkeypatch.setattr(settings, "child_tasks_enabled", True)
    graph = await seed_graph(db, conversations=1)
    ctx = graph.context
    parent = await db.get(Agent, graph.agent_id)
    assert parent is not None
    child = Agent(
        user_id=ctx.user_id,
        org_id=ctx.org_id,
        tenant_id=ctx.tenant_id,
        name="Assigned researcher",
        system_prompt="Research.",
        model_id=parent.model_id,
    )
    db.add(child)
    await db.flush()
    db.add(
        AgentSubAgentLink(
            parent_agent_id=parent.id, sub_agent_id=child.id, delegated_by=ctx.user_id
        )
    )
    await db.commit()
    accepted = await accept_owned_execution(
        db, OwnedExecutionRequest(graph.conversation_ids[0], parent.id, ctx, "chat", None)
    )
    run = await db.get(ConversationRun, accepted.run_id)
    assert run is not None
    run.status, run.is_active = "running", True
    await db.commit()
    return ChildCase(graph, accepted.run_id, child.id)


async def _authorize(
    db: AsyncSession, case: ChildCase, *, require_active: bool = True
) -> ConversationRun:
    return await authorization.authorize_child_target(
        db, case.graph.context, case.run_id, case.child_id, require_active=require_active
    )


async def test_authorized_child_holds_matching_conversation_and_run(
    db: AsyncSession,
    child_case: ChildCase,
) -> None:
    run = await _authorize(db, child_case)
    proof = live_proof(db)
    assert run.id == child_case.run_id and proof is not None
    assert proof.cursor.holds(LockClass.RUN, run.id)
    assert proof.covers_conversation(child_case.graph.conversation_ids[0])
    assert child_case.child_id in proof.agent_ids


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ("interrupted", "parent_terminal"),
        ("completed", "parent_terminal"),
        ("canceling", "cancelled"),
        ("canceled", "cancelled"),
    ],
)
async def test_completed_or_cancelling_parent_blocks_dispatch_but_allows_history(
    db: AsyncSession,
    child_case: ChildCase,
    status: str,
    reason: str,
) -> None:
    await db.execute(
        update(ConversationRun)
        .where(ConversationRun.id == child_case.run_id)
        .values(status=status, is_active=status == "canceling")
    )
    await db.commit()
    with pytest.raises(ChildTaskStopped) as exc:
        await _authorize(db, child_case)
    assert exc.value.reason == reason
    await db.rollback()
    assert (await _authorize(db, child_case, require_active=False)).id == child_case.run_id


async def test_cancel_requested_on_still_running_parent_blocks_dispatch(
    db: AsyncSession,
    child_case: ChildCase,
) -> None:
    await db.execute(
        update(ConversationRun)
        .where(ConversationRun.id == child_case.run_id)
        .values(cancel_requested_at=datetime.now(UTC).replace(tzinfo=None))
    )
    await db.commit()
    with pytest.raises(ChildTaskStopped) as exc:
        await _authorize(db, child_case)
    assert exc.value.reason == "cancelled"


@pytest.mark.parametrize(
    "mutation",
    [
        "removed_member",
        "removed_link",
        "broken_link",
        "archived_agent",
        "company_switch",
        "missing_snapshot",
    ],
)
async def test_revoked_target_is_rechecked_between_calls(
    db: AsyncSession,
    child_case: ChildCase,
    mutation: str,
) -> None:
    ctx = child_case.graph.context
    assert (await _authorize(db, child_case)).id == child_case.run_id
    await db.commit()
    if mutation == "removed_member":
        await db.execute(
            update(OrganizationMember)
            .where(
                OrganizationMember.org_id == ctx.org_id, OrganizationMember.user_id == ctx.user_id
            )
            .values(status="removed")
        )
    elif mutation == "removed_link":
        await db.execute(
            delete(AgentSubAgentLink).where(AgentSubAgentLink.sub_agent_id == child_case.child_id)
        )
    elif mutation == "broken_link":
        await db.execute(
            update(AgentSubAgentLink)
            .where(AgentSubAgentLink.sub_agent_id == child_case.child_id)
            .values(link_status="broken", broken_reason="delegator_lost_access")
        )
    elif mutation == "archived_agent":
        await db.execute(
            update(Agent)
            .where(Agent.id == child_case.child_id)
            .values(archived_at=datetime.now(UTC))
        )
    elif mutation == "company_switch":
        db.add(
            ResourcePolicy(
                tenant_id=ctx.tenant_id, resource_key=f"agent:{child_case.child_id}", enabled=False
            )
        )
    else:
        # An agent currently linked to the parent but never admitted to this run.
        await db.execute(
            delete(ConversationRunAgent).where(
                ConversationRunAgent.agent_id_snapshot == child_case.child_id
            )
        )
    await db.commit()
    with pytest.raises(ChildTaskStopped) as exc:
        await _authorize(db, child_case)
    assert exc.value.reason == "revoked"


async def test_cross_org_target_and_unknown_target_have_same_denial(
    db: AsyncSession,
    child_case: ChildCase,
) -> None:
    stranger = await seed_graph(db, conversations=1)
    for context, child_id in [
        (stranger.context, child_case.child_id),
        (child_case.graph.context, uuid4()),
    ]:
        with pytest.raises(ChildTaskStopped) as exc:
            await authorization.authorize_child_target(db, context, child_case.run_id, child_id)
        assert exc.value.reason == "revoked"
        await db.rollback()


async def _share_child(db: AsyncSession, case: ChildCase) -> None:
    ctx = case.graph.context
    other = User(email=f"child-owner-{uuid4()}@example.com", name="Child owner", is_active=True)
    db.add(other)
    await db.flush()
    db.add_all(
        [
            TenantMember(tenant_id=ctx.tenant_id, user_id=other.id),
            OrganizationMember(org_id=ctx.org_id, tenant_id=ctx.tenant_id, user_id=other.id),
        ]
    )
    await db.execute(update(Agent).where(Agent.id == case.child_id).values(user_id=other.id))
    await db.execute(
        update(ConversationRunAgent)
        .where(
            ConversationRunAgent.run_id == case.run_id,
            ConversationRunAgent.agent_id == case.child_id,
        )
        .values(
            resource_owner_user_id=other.id,
            on_behalf_of_user_id=other.id,
            credential_subject_user_id=other.id,
        )
    )
    await db.commit()


async def test_unavailable_permission_service_propagates_503(
    db: AsyncSession,
    child_case: ChildCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _share_child(db, child_case)
    client = RecordingClient(unavailable=True)
    monkeypatch.setattr(authorization, "current_authz", lambda: service(client))
    with pytest.raises(AppError) as exc:
        await _authorize(db, child_case)
    assert exc.value.status == 503
    assert client.queries


@pytest.mark.parametrize("mode", ["fixed", "per_user"])
async def test_shared_child_rechecks_the_bound_run_subject_each_time(
    db: AsyncSession,
    child_case: ChildCase,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    await _share_child(db, child_case)
    graph = child_case.graph
    await db.execute(update(Agent).where(Agent.id == graph.agent_id).values(identity_mode=mode))
    await db.execute(
        update(ConversationRunAgent)
        .where(
            ConversationRunAgent.run_id == child_case.run_id,
            ConversationRunAgent.agent_id == graph.agent_id,
        )
        .values(identity_mode_at_accept=mode)
    )
    await db.commit()
    caller = f"user:{graph.context.user_id}"
    parent_grant = (caller, "can_run", f"agent:{graph.agent_id}")
    child_grant = (
        caller if mode == "per_user" else f"agent:{graph.agent_id}",
        "can_run",
        f"agent:{child_case.child_id}",
    )
    client = RecordingClient(allowed=frozenset([parent_grant, child_grant]))
    monkeypatch.setattr(authorization, "current_authz", lambda: service(client))
    assert (await _authorize(db, child_case)).id == child_case.run_id
    await db.commit()
    client.allowed = frozenset([parent_grant])
    with pytest.raises(ChildTaskStopped) as exc:
        await _authorize(db, child_case)
    assert exc.value.reason == "revoked"


async def test_authority_epoch_change_during_remote_decision_stops_dispatch(
    db: AsyncSession,
    child_case: ChildCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = child_case.graph
    grant = (f"user:{graph.context.user_id}", "can_run", f"agent:{graph.agent_id}")
    authz = service(RecordingClient(allowed=frozenset([grant])))
    check = authz.check

    async def change_epoch(
        principal: Principal,
        permission: str,
        obj: str,
        *,
        consistency: Consistency = "MINIMIZE_LATENCY",
    ) -> bool:
        allowed = await check(principal, permission, obj, consistency=consistency)
        # Deterministically inject the committed grant change into the gap before locks.
        await db.execute(
            update(Organization)
            .where(Organization.id == graph.context.org_id)
            .values(authz_epoch=Organization.authz_epoch + 1)
        )
        await db.commit()
        return allowed

    monkeypatch.setattr(authz, "check", change_epoch)
    monkeypatch.setattr(authorization, "current_authz", lambda: authz)
    with pytest.raises(ChildTaskStopped) as exc:
        await _authorize(db, child_case)
    assert exc.value.reason == "revoked"


@pytest.mark.parametrize("role", ["root", "subagent"])
@pytest.mark.parametrize("change", ["mode_drift", "pre_upgrade", "subject_drift", "owner_drift"])
async def test_pinned_actor_identity_cannot_change_during_delegation(
    db: AsyncSession,
    child_case: ChildCase,
    role: str,
    change: str,
) -> None:
    # The caller and owner initially coincide. Even when the account UUID happens to
    # match, switching fixed/per-user changes the subject of future delegated decisions.
    agent_id = child_case.graph.agent_id if role == "root" else child_case.child_id
    participant = update(ConversationRunAgent).where(
        ConversationRunAgent.run_id == child_case.run_id, ConversationRunAgent.agent_id == agent_id
    )
    if change == "pre_upgrade":
        await db.execute(participant.values(identity_mode_at_accept=None))
    elif change == "mode_drift":
        await db.execute(update(Agent).where(Agent.id == agent_id).values(identity_mode="per_user"))
    else:
        other = await seed_graph(db, conversations=1)
        if change == "subject_drift":
            await db.execute(participant.values(credential_subject_user_id=other.context.user_id))
        else:
            await db.execute(participant.values(resource_owner_user_id=other.context.user_id))
    await db.commit()
    with pytest.raises(ChildTaskStopped) as exc:
        await _authorize(db, child_case)
    assert exc.value.reason == "revoked"


@pytest.mark.parametrize("prior_pin_changed", [False, True])
async def test_resume_checks_both_original_and_current_pins(
    db: AsyncSession,
    child_case: ChildCase,
    prior_pin_changed: bool,
) -> None:
    graph = child_case.graph
    runtime = await db.get(ConversationRun, child_case.run_id)
    assert runtime is not None
    runtime.status, runtime.is_active, runtime.interrupt_id = "interrupted", False, "approval"
    await db.commit()
    resumed = await accept_owned_execution(
        db,
        OwnedExecutionRequest(
            graph.conversation_ids[0],
            graph.agent_id,
            graph.context,
            "chat",
            None,
            run_source="resume",
            parent_run_id=child_case.run_id,
            interrupt_id="approval",
        ),
    )
    await db.execute(
        update(ConversationRun)
        .where(ConversationRun.id == resumed.run_id)
        .values(status="running", is_active=True)
    )
    if prior_pin_changed:
        await db.execute(
            update(ConversationRunAgent)
            .where(
                ConversationRunAgent.run_id == child_case.run_id,
                ConversationRunAgent.agent_id == child_case.child_id,
            )
            .values(identity_mode_at_accept=None)
        )
    await db.commit()
    await authorization.authorize_child_target(
        db, graph.context, resumed.run_id, child_case.child_id
    )
    if prior_pin_changed:
        with pytest.raises(ChildTaskStopped) as exc:
            await authorization.require_same_child_identity(
                db,
                previous_run_id=child_case.run_id,
                current_run_id=resumed.run_id,
                child_agent_id=child_case.child_id,
            )
        assert exc.value.reason == "revoked"
    else:
        await authorization.require_same_child_identity(
            db,
            previous_run_id=child_case.run_id,
            current_run_id=resumed.run_id,
            child_agent_id=child_case.child_id,
        )
