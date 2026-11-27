"""The default Deep Agents child inherits the original chat root's account and authority."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_agent_builder import build_agent
from app.authz.client import CheckQuery, Consistency
from app.authz.conversation_agent_checks import bind_request_authz
from app.authz.org_context import OrgContext
from app.config import settings
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.emergency_stop import EmergencyStop
from app.models.resource_policy import ResourcePolicy
from app.services.ag_ui_legacy_child_authority import require_legacy_child_binding
from app.services.ag_ui_legacy_child_binding import LegacyChildBinding
from app.services.ag_ui_pending_checkpoint import read_pending_checkpoint
from app.services.agent_execution_acceptance import OwnedExecutionRequest, accept_owned_execution
from app.tenancy.checkpoint_audit import run_configurable
from tests.authz.authz_service_testkit import RecordingClient, service
from tests.authz.execution_gateway_testkit import seed_graph
from tests.authz.test_prepared_runtime_installed_compiler import echo, model, task_call


@dataclass(frozen=True)
class DefaultChild:
    actor: OrgContext
    root: UUID
    chat: Conversation
    run: ConversationRun
    binding: LegacyChildBinding
    grants: RecordingClient

    async def authorize(self, db: AsyncSession) -> UUID:
        return await require_legacy_child_binding(
            db,
            conversation=self.chat,
            context=self.actor,
            parent_run=self.run,
            binding=self.binding,
        )


@pytest.fixture
async def builtin_child(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> DefaultChild:
    monkeypatch.setattr(settings, "child_tasks_enabled", False)
    graph = await seed_graph(db, conversations=1)
    actor = graph.context
    root = graph.agent_id
    grants = RecordingClient(
        allowed=frozenset({(f"user:{actor.user_id}", "can_run", f"agent:{root}")})
    )
    await bind_request_authz(service(grants))
    chat = await db.get(Conversation, graph.conversation_ids[0])
    assert chat
    accepted = await accept_owned_execution(
        db, OwnedExecutionRequest(chat.id, root, actor, "chat", None)
    )
    run = await db.get(ConversationRun, accepted.run_id)
    assert run
    participants = (
        await db.scalars(select(ConversationRunAgent).where(ConversationRunAgent.run_id == run.id))
    ).all()
    assert [pin.role for pin in participants] == ["root"]
    assert participants[0].identity_mode_at_accept is None

    saver = InMemorySaver()
    monkeypatch.setattr("app.agent_runtime.checkpointer.get_checkpointer", lambda: saver)
    engine = build_agent(
        model(
            task_call("general-purpose", "gp-call"),
            AIMessage(
                content="",
                tool_calls=[{"name": "echo", "args": {"text": "Please review"}, "id": "echo-call"}],
            ),
            AIMessage(content="Reviewed."),
        ),
        [echo],
        "Coordinate a review.",
        interrupt_on={"echo": {"allowed_decisions": ["approve", "reject"]}},
        checkpointer=saver,
    )
    paused = await engine.ainvoke(
        {"messages": [("user", "Request a review.")]},
        {"configurable": run_configurable(str(chat.id), run.id, actor.tenant_id)},
    )
    (interrupt,) = paused["__interrupt__"]
    run.status, run.is_active, run.interrupt_id = "interrupted", False, interrupt.id
    await db.commit()
    live = await read_pending_checkpoint(db, conversation=chat, context=actor, parent_run=run)
    assert live is not None
    binding = live.legacy_child_bindings[interrupt.id]
    assert binding is not None and binding.tool_call_id == "gp-call"
    assert binding.runtime_name == "general-purpose" and binding.producer_run_id == run.id
    return DefaultChild(actor, root, chat, run, binding, grants)


async def test_implicit_child_has_root_only_authority_and_current_root_grant(
    db: AsyncSession, builtin_child: DefaultChild, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = builtin_child
    observed = []
    original = case.grants.check

    async def record(query: CheckQuery, *, consistency: Consistency = "MINIMIZE_LATENCY") -> bool:
        observed.append(consistency)
        return await original(query, consistency=consistency)

    monkeypatch.setattr(case.grants, "check", record)
    assert await case.authorize(db) == case.root
    assert observed == ["HIGHER_CONSISTENCY"]
    await db.commit()
    case.grants.allowed = frozenset()
    with pytest.raises(AppError) as denied:
        await case.authorize(db)
    assert denied.value.status == 404


@pytest.mark.parametrize(
    "changed", ["archived", "emergency_stop", "task_disabled", "account", "grant_scope"]
)
async def test_root_control_changes_block_implicit_child(
    db: AsyncSession, builtin_child: DefaultChild, changed: str
) -> None:
    case = builtin_child
    if changed == "archived":
        await db.execute(
            update(Agent).where(Agent.id == case.root).values(archived_at=datetime.now(UTC))
        )
    elif changed == "emergency_stop":
        db.add(
            EmergencyStop(
                tenant_id=case.actor.tenant_id,
                org_id=case.actor.org_id,
                scope_type="agent",
                scope_id=case.root,
                scope_name="Root",
                reason="Stop review",
                status="active",
                stopped_at=datetime.now(UTC),
            )
        )
    elif changed == "task_disabled":
        db.add(
            ResourcePolicy(
                tenant_id=case.actor.tenant_id, resource_key="builtin:task", enabled=False
            )
        )
    elif changed == "account":
        await db.execute(
            update(ConversationRunAgent)
            .where(ConversationRunAgent.run_id == case.run.id)
            .values(credential_subject_user_id=None)
        )
    else:
        stranger = await seed_graph(db, conversations=1)
        case = replace(case, actor=stranger.context)
    await db.commit()
    with pytest.raises(AppError) as denied:
        await case.authorize(db)
    assert denied.value.status == 404


@pytest.mark.parametrize("changed", ["hash", "source", "version"])
async def test_implicit_child_requires_the_admitted_conversation_policy(
    db: AsyncSession, builtin_child: DefaultChild, changed: str
) -> None:
    case = builtin_child
    run = case.run
    if changed == "hash":
        run.runtime_policy_hash = "0" * 64
    elif changed == "source":
        run.runtime_policy_source = (
            "stored" if run.runtime_policy_source != "stored" else "legacy_compat"
        )
    else:
        run.runtime_policy_version = (run.runtime_policy_version or 0) + 1
    await db.commit()
    with pytest.raises(AppError) as denied:
        await case.authorize(db)
    assert denied.value.status == 404


async def test_display_name_cannot_select_implicit_parent_authority(
    db: AsyncSession, builtin_child: DefaultChild
) -> None:
    alias = replace(builtin_child.binding, runtime_name="General Purpose")
    with pytest.raises(AppError) as denied:
        await replace(builtin_child, binding=alias).authorize(db)
    assert denied.value.status == 404


async def test_default_child_rechecks_origin_policy_and_requires_a_unique_producer_on_resume(
    db: AsyncSession, builtin_child: DefaultChild
) -> None:
    case = builtin_child
    accepted = await accept_owned_execution(
        db,
        OwnedExecutionRequest(
            case.chat.id,
            case.root,
            case.actor,
            "chat",
            None,
            run_source="resume",
            parent_run_id=case.run.id,
            interrupt_id=case.run.interrupt_id,
        ),
    )
    continuation = await db.get(ConversationRun, accepted.run_id)
    assert continuation
    continuation.status, continuation.is_active = "interrupted", False
    await db.commit()
    next_case = replace(case, run=continuation)
    assert await next_case.authorize(db) == case.root
    with pytest.raises(AppError) as unknown_producer:
        await replace(next_case, binding=replace(case.binding, producer_run_id=None)).authorize(db)
    assert unknown_producer.value.status == 404
    case.run.runtime_policy_hash = "f" * 64
    await db.commit()
    for producer in (case.run.id, continuation.id):
        # A later graph can issue another task. It still inherits the original
        # root's execution policy rather than a new grant from the continuation.
        with pytest.raises(AppError) as changed_origin:
            await replace(
                next_case, binding=replace(case.binding, producer_run_id=producer)
            ).authorize(db)
        assert changed_origin.value.status == 404
