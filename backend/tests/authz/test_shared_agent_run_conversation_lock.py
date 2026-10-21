"""A run of my conversation with someone else's agent locks under enforced sharing.

Found on a live enforced stack: ``lock_run_conversation`` confirmed the agent with the
owner-only predicate, so every chat with a shared agent ended in 404 CONVERSATION_NOT_FOUND
after ``agent#can_run`` had already allowed it. Legacy keeps the owner-only answer.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import runtime
from app.authz.conversation_run_scope import RunConversationTarget, lock_run_conversation
from app.authz.fake import FakeAuthz
from app.authz.service import AuthzService
from app.models.agent import Agent
from app.models.organization import OrganizationMember, TenantMember
from app.models.user import User
from tests.authz.execution_gateway_testkit import seed_graph


async def _shared(db: AsyncSession) -> RunConversationTarget:
    graph = await seed_graph(db, conversations=1)
    owner = User(email="shared-owner@example.com", name="Owner", is_active=True)
    db.add(owner)
    await db.flush()
    ctx = graph.context
    db.add_all(
        (
            TenantMember(tenant_id=ctx.tenant_id, user_id=owner.id),
            OrganizationMember(org_id=ctx.org_id, tenant_id=ctx.tenant_id, user_id=owner.id),
        )
    )
    agent = await db.get(Agent, graph.agent_id)
    assert agent is not None
    agent.user_id = owner.id  # my conversation, someone else's agent
    await db.commit()
    return RunConversationTarget(graph.conversation_ids[0], graph.agent_id, ctx)


@pytest.mark.asyncio
async def test_enforced_sharing_locks_my_conversation_with_a_shared_agent(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = await _shared(db)
    monkeypatch.setattr(runtime, "_current", AuthzService(FakeAuthz(frozenset()), mode="enforce"))
    locked = await lock_run_conversation(db, target)
    assert locked is not None and locked.id == target.conversation_id


@pytest.mark.asyncio
async def test_legacy_keeps_the_owner_only_answer(db: AsyncSession) -> None:
    target = await _shared(db)
    assert await lock_run_conversation(db, target) is None
