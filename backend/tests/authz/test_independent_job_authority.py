from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.independent_job_authority import (
    authorize_independent_target,
    list_independent_targets,
    require_job_authority,
)
from app.authz.org_context import OrgContext
from app.credentials import service as credential_service
from app.credentials.creation import CredentialCreation
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.emergency_stop import EmergencyStop
from app.models.independent_chat_job import IndependentChatJob
from app.models.organization import OrganizationMember
from app.services.agent_execution_acceptance import OwnedExecutionRequest, accept_owned_execution
from tests.authz.unit_scope_fixture import unit_context
from tests.test_agent_lifecycle_run_ownership import (
    _seed_execution_tree,
    _skip_sqlite_advisory_locks,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def delegation(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> tuple[OrgContext, ConversationRun, ConversationRun, IndependentChatJob]:
    await _skip_sqlite_advisory_locks(monkeypatch)
    root, child, origin = await _seed_execution_tree(db)
    child.runtime_policy = {"version": 1}
    await db.flush()
    context = unit_context(root.user_id)
    await credential_service.create(
        db,
        CredentialCreation(
            definition_key="openai",
            name="Authority test key",
            data={"api_key": "dummy-authority-test-key"},
        ),
        context,
    )
    accepted = await accept_owned_execution(
        db, OwnedExecutionRequest(origin.id, root.id, context, "chat", None)
    )
    await db.commit()
    destination = Conversation(
        agent_id=child.id,
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        source="independent_job",
        agent_name_snapshot=child.name,
        title="Independent",
        last_activity_at=datetime.now(UTC),
    )
    db.add(destination)
    await db.flush()
    execution = await accept_owned_execution(
        db, OwnedExecutionRequest(destination.id, child.id, context, "chat", None)
    )
    await db.commit()
    parent_run = await db.get(ConversationRun, accepted.run_id)
    child_run = await db.get(ConversationRun, execution.run_id)
    assert parent_run and child_run
    _, fingerprint = await authorize_independent_target(db, parent_run, context, child.id)
    job = IndependentChatJob(
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        parent_agent_id=root.id,
        parent_run_id=parent_run.id,
        parent_conversation_id=origin.id,
        child_agent_id=child.id,
        child_run_id=child_run.id,
        child_conversation_id=destination.id,
        request_id=uuid.uuid4(),
        request_digest="0" * 64,
        capability_sha256=fingerprint,
    )
    db.add(job)
    await db.commit()
    return context, parent_run, child_run, job


async def test_parent_finish_keeps_independent_child_authorized(
    db: AsyncSession,
    delegation: tuple[OrgContext, ConversationRun, ConversationRun, IndependentChatJob],
) -> None:
    context, parent, _, job = delegation
    parent.status, parent.is_active = "completed", False
    await db.commit()
    assert [agent.id for agent in await list_independent_targets(db, parent, context)] == [
        job.child_agent_id
    ]
    await db.commit()
    await require_job_authority(db, job, context)


@pytest.mark.parametrize(
    "change",
    [
        "delegation_removed",
        "broken",
        "participant_path",
        "suspended",
        "cancel",
        "policy",
        "child_source",
        "wrong_actor",
        "wrong_org",
        "wrong_tenant",
        "archived",
        "parent_emergency",
        "child_emergency",
        "capabilities",
        "indirect_origin",
    ],
)
async def test_dispatch_rechecks_current_scope_and_recorded_participant(
    db: AsyncSession,
    delegation: tuple[OrgContext, ConversationRun, ConversationRun, IndependentChatJob],
    change: str,
) -> None:
    context, parent, child, job = delegation
    if change in {"delegation_removed", "broken"}:
        link = await db.get(AgentSubAgentLink, (job.parent_agent_id, job.child_agent_id))
        assert link
        if change == "delegation_removed":
            await db.delete(link)
        else:
            link.link_status = "broken"
    elif change == "participant_path":
        participant = await db.scalar(
            select(ConversationRunAgent).where(
                ConversationRunAgent.run_id == parent.id, ConversationRunAgent.role == "subagent"
            )
        )
        assert participant
        participant.path = [str(job.parent_agent_id), str(uuid.uuid4()), str(job.child_agent_id)]
    elif change == "suspended":
        member = await db.scalar(
            select(OrganizationMember).where(
                OrganizationMember.org_id == context.org_id,
                OrganizationMember.user_id == context.user_id,
            )
        )
        assert member
        member.status = "suspended"
    elif change == "cancel":
        job.cancel_requested_at = datetime.now(UTC)
    elif change == "policy":
        child.runtime_policy_hash = "e" * 64
    elif change == "child_source":
        destination = await db.get(Conversation, job.child_conversation_id)
        assert destination
        destination.source = "ui"
    elif change in {"parent_emergency", "child_emergency"}:
        db.add(
            EmergencyStop(
                tenant_id=context.tenant_id,
                scope_type="agent",
                scope_id=job.parent_agent_id
                if change == "parent_emergency"
                else job.child_agent_id,
                reason="test",
                stopped_at=datetime.now(UTC),
            )
        )
    elif change == "capabilities":
        target = await db.get(Agent, job.child_agent_id)
        assert target
        target.system_prompt += " changed after handoff"
    elif change == "indirect_origin":
        origin = await db.get(Conversation, job.parent_conversation_id)
        assert origin
        origin.source = "independent_job"
    elif change == "archived":
        target = await db.get(Agent, job.child_agent_id)
        assert target
        target.archived_at = datetime.now(UTC)
    else:
        context = OrgContext(
            user_id=uuid.uuid4() if change == "wrong_actor" else context.user_id,
            tenant_id=uuid.uuid4() if change == "wrong_tenant" else context.tenant_id,
            org_id=uuid.uuid4() if change == "wrong_org" else context.org_id,
        )
    await db.commit()
    with pytest.raises(AppError):
        await require_job_authority(db, job, context)


async def test_each_send_observes_revoked_child_grant(
    db: AsyncSession,
    delegation: tuple[OrgContext, ConversationRun, ConversationRun, IndependentChatJob],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.authz import runtime
    from app.authz.fake import FakeAuthz
    from app.authz.run_decisions import run_decision_scope
    from app.authz.service import AuthzService

    context, _, _, job = delegation
    actor = f"user:{context.user_id}"
    root = (actor, "can_run", f"agent:{job.parent_agent_id}")
    child = (actor, "can_run", f"agent:{job.child_agent_id}")

    def grants(*rules: tuple[str, str, str]) -> None:
        monkeypatch.setattr(
            runtime,
            "_current",
            AuthzService(
                FakeAuthz(frozenset(rules)), mode="enforce", enforce_types=frozenset({"agent"})
            ),
        )

    # A long-lived worker task may carry its caller's authorization memo. Each
    # dispatch opens a fresh one so the second cannot inherit the first grant.
    with run_decision_scope():
        grants(root, child)
        await require_job_authority(db, job, context)
        await db.commit()
        grants(root)
        with pytest.raises(AppError) as rejected:
            await require_job_authority(db, job, context)
        assert rejected.value.status == 404
