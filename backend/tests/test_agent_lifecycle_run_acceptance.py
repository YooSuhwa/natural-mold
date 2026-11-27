from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Literal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.agent import Agent
from app.models.agent_api import AgentApiRun
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.services import agent_execution_owner, conversation_run_service
from app.services.agent_execution_owner import (
    ExecutionEnvelopeRef,
    OwnedExecutionRequest,
    OwnedExecutionSource,
)
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, unit_context
from tests.test_agent_lifecycle_run_ownership import (
    _seed_execution_tree,
    _skip_sqlite_advisory_locks,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["chat", "api", "trigger"])
async def test_all_sources_accept_one_run_complete_participants_and_exact_envelope(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    source: OwnedExecutionSource,
) -> None:
    await _skip_sqlite_advisory_locks(monkeypatch)
    monkeypatch.setattr(settings, "child_tasks_enabled", True)
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
    assert [row.identity_mode_at_accept for row in rows] == (
        [root.identity_mode, child.identity_mode] if source == "chat" else [None, None]
    )
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


async def _identity_modes(db: AsyncSession, run_id: uuid.UUID) -> dict[uuid.UUID, str | None]:
    rows = await db.execute(
        select(
            ConversationRunAgent.agent_id_snapshot, ConversationRunAgent.identity_mode_at_accept
        ).where(ConversationRunAgent.run_id == run_id)
    )
    return dict(rows.tuples().all())


@pytest.mark.parametrize(("initially_enabled", "enabled_on_resume"), [(True, False), (False, True)])
async def test_chat_resume_keeps_participant_identity_pins_from_the_interrupted_run(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    initially_enabled: bool,
    enabled_on_resume: bool,
) -> None:
    await _skip_sqlite_advisory_locks(monkeypatch)
    root, child, conversation = await _seed_execution_tree(db)
    root.identity_mode = "per_user"
    child.identity_mode = "fixed"
    await db.commit()
    request = OwnedExecutionRequest(
        conversation_id=conversation.id,
        root_agent_id=root.id,
        context=unit_context(root.user_id),
        source="chat",
        input_preview="Research",
    )

    monkeypatch.setattr(settings, "child_tasks_enabled", initially_enabled)
    accepted = await agent_execution_owner.accept_owned_execution(db, request)
    await db.commit()
    expected = (
        {root.id: "per_user", child.id: "fixed"}
        if initially_enabled
        else {root.id: None, child.id: None}
    )
    assert await _identity_modes(db, accepted.run_id) == expected

    run = await db.get(ConversationRun, accepted.run_id, with_for_update=True)
    assert run is not None
    await conversation_run_service.transition_run(db, run, "running", worker_instance_id="owner")
    await conversation_run_service.transition_run(db, run, "interrupted", interrupt_id="approval")
    await db.commit()
    # A resumed approval must not promote a newly linked participant or rewrite who
    # an already running child acted as, even when all three agents have one owner.
    root.identity_mode, child.identity_mode = "fixed", "per_user"
    sibling = Agent(
        user_id=root.user_id,
        org_id=root.org_id,
        tenant_id=root.tenant_id,
        name="Late participant",
        system_prompt="Follow up",
        model_id=root.model_id,
    )
    db.add(sibling)
    await db.flush()
    db.add(AgentSubAgentLink(parent_agent_id=root.id, sub_agent_id=sibling.id, position=1))
    await db.commit()

    monkeypatch.setattr(settings, "child_tasks_enabled", enabled_on_resume)
    resumed = await agent_execution_owner.accept_owned_execution(
        db, replace(request, run_source="resume", parent_run_id=run.id, interrupt_id="approval")
    )
    await db.commit()
    assert await _identity_modes(db, resumed.run_id) == {**expected, sibling.id: None}
    assert await _identity_modes(db, accepted.run_id) == expected


@pytest.mark.parametrize("source", ["edit", "regenerate", "resume"])
async def test_child_identity_control_for_forks_and_legacy_resumes(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    source: Literal["edit", "regenerate", "resume"],
) -> None:
    await _skip_sqlite_advisory_locks(monkeypatch)
    monkeypatch.setattr(settings, "child_tasks_enabled", True)
    root, child, conversation = await _seed_execution_tree(db)
    request = OwnedExecutionRequest(
        conversation.id, root.id, unit_context(root.user_id), "chat", "Research"
    )
    accepted = await agent_execution_owner.accept_owned_execution(db, request)
    await db.commit()
    assert await _identity_modes(db, accepted.run_id) == {
        root.id: root.identity_mode,
        child.id: child.identity_mode,
    }
    run = await db.get(ConversationRun, accepted.run_id, with_for_update=True)
    assert run is not None
    await conversation_run_service.transition_run(db, run, "running", worker_instance_id="owner")
    await conversation_run_service.transition_run(db, run, "completed")
    await db.commit()

    if source == "resume":
        # Older persisted interrupts have no authoritative parent participant list.
        followup = replace(
            request, run_source=source, interrupt_id="legacy", allow_legacy_resume=True
        )
    else:
        # A fork is freshly admitted under the current flag despite its parent reference.
        monkeypatch.setattr(settings, "child_tasks_enabled", False)
        followup = replace(request, run_source=source, parent_run_id=run.id)
    next_run = await agent_execution_owner.accept_owned_execution(db, followup)
    assert await _identity_modes(db, next_run.run_id) == {root.id: None, child.id: None}
