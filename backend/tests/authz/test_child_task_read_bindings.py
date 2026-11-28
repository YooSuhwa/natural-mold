"""Replaying a private child uses the current grant and the exact durable invocation."""

from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState

from app.authz import child_task_authorization as authorization
from app.authz.client import CheckQuery, Consistency
from app.authz.principal import Principal
from app.child_tasks.persistence import begin_child_task
from app.config import settings
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.conversation_child_task import ConversationChildTask
from app.models.conversation_run import ConversationRun
from app.models.organization import Organization, OrganizationMember, TenantMember
from app.services.agent_execution_acceptance import OwnedExecutionRequest, accept_owned_execution
from app.services.child_task_read_bindings import (
    readable_child_task_bindings,
    readable_child_task_visibility,
)
from tests.authz.authz_service_testkit import RecordingClient, service
from tests.authz.execution_gateway_testkit import seed_graph
from tests.authz.test_child_task_authorization import ChildCase, _share_child
from tests.authz.test_child_task_authorization import child_case as child_case

CALL = "call-research"
NAMESPACE = "tools:parent|task:research"
VISIBLE = (False, frozenset({CALL}), frozenset({("tools:parent", "task:research")}))
HIDDEN = (False, frozenset(), frozenset())


@pytest.fixture
async def bound(db: AsyncSession, child_case: ChildCase) -> Conversation:
    graph = child_case.graph
    task_id = await begin_child_task(
        db,
        context=graph.context,
        conversation_id=graph.conversation_ids[0],
        run_id=child_case.run_id,
        tool_call_id=CALL,
        child_agent_id=child_case.child_id,
        role="research",
        checkpoint_ns=NAMESPACE,
    )
    assert task_id is not None
    await db.commit()
    conversation = await db.get(Conversation, graph.conversation_ids[0])
    assert conversation is not None
    return conversation


async def test_bindings_are_select_only_and_revoke_on_next_page(
    db: AsyncSession, bound: Conversation, child_case: ChildCase
) -> None:
    ctx = child_case.graph.context

    def assert_unlocked_select(action: ORMExecuteState) -> None:
        assert action.is_select
        assert getattr(action.statement, "_for_update_arg", None) is None

    def forbid_transaction_change(*_args: object) -> None:
        pytest.fail("Read-only visibility must leave the caller transaction intact")

    listeners = [
        ("do_orm_execute", assert_unlocked_select),
        ("before_flush", forbid_transaction_change),
        ("before_commit", forbid_transaction_change),
        ("after_rollback", forbid_transaction_change),
    ]
    for key, listener in listeners:
        event.listen(db.sync_session, key, listener)
    try:
        assert await readable_child_task_bindings(db, bound, ctx, child_case.run_id) == VISIBLE
    finally:
        for key, listener in listeners:
            event.remove(db.sync_session, key, listener)
    await db.execute(
        delete(AgentSubAgentLink).where(AgentSubAgentLink.sub_agent_id == child_case.child_id)
    )
    await db.commit()
    assert await readable_child_task_bindings(db, bound, ctx, child_case.run_id) == HIDDEN


async def test_replay_triples_keep_task_call_and_checkpoint_together(
    db: AsyncSession, bound: Conversation, child_case: ChildCase
) -> None:
    context = child_case.graph.context
    second = await begin_child_task(
        db,
        context=context,
        conversation_id=bound.id,
        run_id=child_case.run_id,
        tool_call_id="call-second",
        child_agent_id=child_case.child_id,
        role="research",
        checkpoint_ns="tools:second",
    )
    await db.commit()
    first = await db.scalar(
        select(ConversationChildTask.id).where(
            ConversationChildTask.run_id == child_case.run_id,
            ConversationChildTask.tool_call_id == CALL,
        )
    )
    visibility = await readable_child_task_visibility(db, bound, context, child_case.run_id)
    assert first and second
    assert visibility.bindings == frozenset(
        {
            (CALL, ("tools:parent", "task:research"), str(first)),
            ("call-second", ("tools:second",), str(second)),
        }
    )
    await db.execute(
        update(ConversationChildTask)
        .where(ConversationChildTask.id == second)
        .values(checkpoint_ns=NAMESPACE)
    )
    await db.commit()
    assert not (
        await readable_child_task_visibility(db, bound, context, child_case.run_id)
    ).bindings


async def test_bindings_require_private_owner_org_and_requested_run(
    db: AsyncSession, bound: Conversation, child_case: ChildCase
) -> None:
    ctx = child_case.graph.context
    other = await seed_graph(db, conversations=1)
    stranger = other.context
    db.add_all(
        [
            # A stale row in a separate child group must reveal neither its call nor namespace.
            ConversationChildTask(
                run_id=child_case.run_id,
                current_run_id=child_case.run_id,
                tool_call_id="hidden-call",
                checkpoint_ns="hidden:namespace",
                child_agent_id=other.agent_id,
                role="hidden",
            ),
            TenantMember(tenant_id=ctx.tenant_id, user_id=stranger.user_id),
            OrganizationMember(
                org_id=ctx.org_id, tenant_id=ctx.tenant_id, user_id=stranger.user_id
            ),
        ]
    )
    await db.commit()
    assert await readable_child_task_bindings(db, bound, ctx, child_case.run_id) == VISIBLE
    await db.execute(
        update(ConversationChildTask)
        .where(ConversationChildTask.child_agent_id == other.agent_id)
        .values(checkpoint_ns=f"{NAMESPACE}|task:nested")
    )
    await db.commit()
    child_calls_only = (False, frozenset({CALL}), frozenset())
    assert await readable_child_task_bindings(db, bound, ctx, child_case.run_id) == child_calls_only
    for context, run_id in (
        (stranger, child_case.run_id),
        (replace(ctx, user_id=stranger.user_id), child_case.run_id),
        (ctx, uuid4()),
    ):
        assert await readable_child_task_bindings(db, bound, context, run_id) == HIDDEN


async def test_shared_fixed_parent_loses_child_grant_between_pages(
    db: AsyncSession, bound: Conversation, child_case: ChildCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = child_case.graph
    await _share_child(db, child_case)
    root_grant = (f"user:{graph.context.user_id}", "can_run", f"agent:{graph.agent_id}")
    child_grant = (f"agent:{graph.agent_id}", "can_run", f"agent:{child_case.child_id}")
    client = RecordingClient(allowed=frozenset([root_grant, child_grant]))
    monkeypatch.setattr(authorization, "current_authz", lambda: service(client))

    assert (
        await readable_child_task_bindings(db, bound, graph.context, child_case.run_id) == VISIBLE
    )
    assert {
        (q.user, q.relation, q.object) for q in client.queries if isinstance(q, CheckQuery)
    } == {
        root_grant,
        child_grant,
    }
    client.allowed = frozenset([root_grant])
    assert await readable_child_task_bindings(db, bound, graph.context, child_case.run_id) == HIDDEN
    client.unavailable = True
    assert await readable_child_task_bindings(db, bound, graph.context, child_case.run_id) == HIDDEN


@pytest.mark.parametrize("mutation", ["epoch", "binding"])
async def test_binding_changed_during_remote_grant_is_hidden(
    db: AsyncSession,
    bound: Conversation,
    child_case: ChildCase,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    graph = child_case.graph
    grant = (f"user:{graph.context.user_id}", "can_run", f"agent:{graph.agent_id}")
    authz = service(RecordingClient(allowed=frozenset([grant])))
    check = authz.check

    async def change_after_check(
        principal: Principal, permission: str, obj: str, *, consistency: Consistency
    ) -> bool:
        assert consistency == "HIGHER_CONSISTENCY"
        allowed = await check(principal, permission, obj, consistency=consistency)
        if mutation == "epoch":
            await db.execute(
                update(Organization)
                .where(Organization.id == graph.context.org_id)
                .values(authz_epoch=Organization.authz_epoch + 1)
            )
        else:
            await db.execute(
                update(ConversationChildTask)
                .where(ConversationChildTask.run_id == child_case.run_id)
                .values(checkpoint_ns="other", tool_call_id="different")
            )
        await db.commit()
        return allowed

    monkeypatch.setattr(authorization, "current_authz", lambda: authz)
    monkeypatch.setattr(authz, "check", change_after_check)
    assert await readable_child_task_bindings(db, bound, graph.context, child_case.run_id) == HIDDEN


async def test_resumed_task_requires_both_admitted_identities(
    db: AsyncSession, bound: Conversation, child_case: ChildCase
) -> None:
    graph = child_case.graph
    run_ids = [child_case.run_id]
    for index in range(3):
        previous = run_ids[-1]
        interrupt_id = f"approval-{index}"
        await db.execute(
            update(ConversationRun)
            .where(ConversationRun.id == previous)
            .values(status="interrupted", is_active=False, interrupt_id=interrupt_id)
        )
        await db.commit()
        resumed = await accept_owned_execution(
            db,
            OwnedExecutionRequest(
                bound.id,
                graph.agent_id,
                graph.context,
                "chat",
                None,
                run_source="resume",
                parent_run_id=previous,
                interrupt_id=interrupt_id,
            ),
        )
        run_ids.append(resumed.run_id)
    # The alternate tip has the same valid actor and participants as the replayed
    # nested resume chain. Only the durable parent pointers distinguish the branches.
    sibling = run_ids.pop()
    await db.execute(
        update(ConversationRun)
        .where(ConversationRun.id == sibling)
        .values(parent_run_id=run_ids[0], interrupt_id="approval-0")
    )
    await db.execute(
        update(ConversationChildTask)
        .where(ConversationChildTask.run_id == child_case.run_id)
        .values(current_run_id=run_ids[-1])
    )
    await db.commit()
    assert await readable_child_task_bindings(db, bound, graph.context, sibling) == HIDDEN
    for run_id in run_ids:
        assert await readable_child_task_bindings(db, bound, graph.context, run_id) == VISIBLE
    await db.execute(
        update(ConversationRunAgent)
        .where(
            ConversationRunAgent.run_id == child_case.run_id,
            ConversationRunAgent.agent_id == child_case.child_id,
        )
        .values(identity_mode_at_accept=None)
    )
    await db.commit()
    for run_id in run_ids:
        assert await readable_child_task_bindings(db, bound, graph.context, run_id) == HIDDEN


async def test_legacy_visibility_requires_feature_off_and_no_root_pin_across_conversation(
    db: AsyncSession, child_case: ChildCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = child_case.graph
    conversation = await db.get(Conversation, graph.conversation_ids[0])
    assert conversation is not None
    # A separate historical run in the same conversation precedes the first admission pin.
    old = ConversationRun(
        conversation_id=conversation.id,
        agent_id=graph.agent_id,
        user_id=graph.context.user_id,
        org_id=graph.context.org_id,
        tenant_id=graph.context.tenant_id,
        run_source="CHAT",
        source="chat",
        status="completed",
    )
    db.add(old)
    await db.commit()
    monkeypatch.setattr(settings, "child_tasks_enabled", False)
    assert await readable_child_task_bindings(db, conversation, graph.context, old.id) == HIDDEN
    await db.execute(
        delete(ConversationRunAgent).where(ConversationRunAgent.run_id == child_case.run_id)
    )
    await db.commit()
    legacy = (True, frozenset(), frozenset())
    assert await readable_child_task_bindings(db, conversation, graph.context, old.id) == legacy
    old.run_source = None
    await db.commit()
    assert await readable_child_task_bindings(db, conversation, graph.context, old.id) == legacy
    old.run_source = "API"
    await db.commit()
    assert await readable_child_task_bindings(db, conversation, graph.context, old.id) == HIDDEN
    other = await seed_graph(db, conversations=1)
    old.run_source, old.org_id, old.tenant_id = (
        "CHAT",
        other.context.org_id,
        other.context.tenant_id,
    )
    await db.commit()
    assert await readable_child_task_bindings(db, conversation, graph.context, old.id) == HIDDEN
    old.org_id, old.tenant_id = graph.context.org_id, graph.context.tenant_id
    await db.commit()
    monkeypatch.setattr(settings, "child_tasks_enabled", True)
    assert await readable_child_task_bindings(db, conversation, graph.context, old.id) == HIDDEN
    monkeypatch.setattr(settings, "child_tasks_enabled", False)
    conversation.source = INDEPENDENT_JOB_SOURCE
    await db.commit()
    assert await readable_child_task_bindings(db, conversation, graph.context, old.id) == HIDDEN
