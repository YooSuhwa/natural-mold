"""Replaying an independent conversation needs a current local and remote grant."""

from collections.abc import Sequence
from uuid import uuid4

import pytest
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.client import CheckQuery, Consistency
from app.authz.fake import FakeAuthz
from app.authz.independent_job_history import readable_job_conversations
from app.authz.org_context import OrgContext
from app.authz.service import AuthzService
from app.dependencies import CurrentUser
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation_run import ConversationRun
from app.models.independent_chat_job import IndependentChatJob
from app.models.organization import Organization, OrganizationMember
from tests.authz.test_independent_job_authority import delegation as delegation
from tests.authz.unit_scope_fixture import ensure_unit_scope

type Delegation = tuple[OrgContext, ConversationRun, ConversationRun, IndependentChatJob]


@pytest.mark.parametrize("parent_identity", ["per_user", "fixed"])
async def test_history_checks_each_current_principal_after_revocation(
    db: AsyncSession, delegation: Delegation, monkeypatch: pytest.MonkeyPatch, parent_identity: str
) -> None:
    context, _, _, job = delegation
    publisher = CurrentUser(id=uuid4(), email="child-publisher@example.test", name="Publisher")
    await ensure_unit_scope(db, publisher)
    await db.execute(
        update(Agent).where(Agent.id == job.parent_agent_id).values(identity_mode=parent_identity)
    )
    await db.execute(
        update(Agent).where(Agent.id == job.child_agent_id).values(user_id=publisher.id)
    )
    await db.commit()
    caller = f"user:{context.user_id}"
    parent, child = f"agent:{job.parent_agent_id}", f"agent:{job.child_agent_id}"
    grants = frozenset(
        (subject, "can_run", obj)
        for subject, obj in ((caller, parent), (caller, child), (parent, child))
    )
    enforcer = AuthzService(FakeAuthz(grants), mode="enforce")
    seen: set[tuple[str, Consistency]] = set()
    revoked_subject = parent if parent_identity == "fixed" else caller
    revoked = False
    batch = FakeAuthz.batch_check

    async def observe(
        client: FakeAuthz,
        queries: Sequence[CheckQuery],
        *,
        consistency: Consistency = "MINIMIZE_LATENCY",
    ) -> tuple[bool, ...]:
        seen.update((q.user, consistency) for q in queries)
        answers = await batch(client, queries, consistency=consistency)
        # The replica may still return its previous allow for this one grant.
        return tuple(
            allowed
            and not (
                revoked
                and consistency == "HIGHER_CONSISTENCY"
                and q.user == revoked_subject
                and q.object == child
            )
            for q, allowed in zip(queries, answers, strict=True)
        )

    monkeypatch.setattr(FakeAuthz, "batch_check", observe)
    assert await readable_job_conversations(db, context, job_ids=[job.id], authz=enforcer) == {
        job.id: job.child_conversation_id
    }
    revoked = True
    assert not await readable_job_conversations(db, context, job_ids=[job.id], authz=enforcer)
    subjects = {parent, caller} if parent_identity == "fixed" else {caller}
    assert seen == {(subject, "HIGHER_CONSISTENCY") for subject in subjects}


@pytest.mark.parametrize("changed", ["membership", "delegation", "identity", "epoch"])
async def test_local_change_during_remote_allow_hides_job_in_same_page(
    db: AsyncSession, delegation: Delegation, monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    context, _, _, job = delegation
    caller = f"user:{context.user_id}"
    grants = frozenset(
        (caller, "can_run", f"agent:{target}")
        for target in (job.parent_agent_id, job.child_agent_id)
    )
    enforcer = AuthzService(FakeAuthz(grants), mode="enforce")
    batch = FakeAuthz.batch_check
    updated = False

    async def change_while_checking(
        client: FakeAuthz,
        queries: Sequence[CheckQuery],
        *,
        consistency: Consistency = "MINIMIZE_LATENCY",
    ) -> tuple[bool, ...]:
        nonlocal updated
        answers = await batch(client, queries, consistency=consistency)
        if updated:
            return answers
        updated = True
        async with AsyncSession(db.bind) as writer, writer.begin():
            if changed == "membership":
                await writer.execute(
                    delete(OrganizationMember).where(
                        OrganizationMember.org_id == context.org_id,
                        OrganizationMember.user_id == context.user_id,
                    )
                )
            elif changed == "delegation":
                await writer.execute(
                    delete(AgentSubAgentLink).where(
                        AgentSubAgentLink.parent_agent_id == job.parent_agent_id,
                        AgentSubAgentLink.sub_agent_id == job.child_agent_id,
                    )
                )
            elif changed == "identity":
                await writer.execute(
                    update(Agent)
                    .where(Agent.id == job.parent_agent_id)
                    .values(identity_mode="per_user")
                )
            else:
                await writer.execute(
                    update(Organization)
                    .where(Organization.id == context.org_id)
                    .values(authz_epoch=Organization.authz_epoch + 1)
                )
        return answers

    monkeypatch.setattr(FakeAuthz, "batch_check", change_while_checking)
    assert not await readable_job_conversations(db, context, job_ids=[job.id], authz=enforcer)
    assert updated
