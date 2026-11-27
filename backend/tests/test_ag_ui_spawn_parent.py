"""Expose a public parent only when the run is the initial, still-readable delegated job."""

from collections.abc import Sequence
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import delete, event
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import runtime
from app.authz.client import CheckQuery, Consistency
from app.authz.fake import FakeAuthz
from app.authz.org_context import OrgContext
from app.authz.service import AuthzService
from app.models.ag_ui_command_receipt import AgUiCommandReceipt
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.independent_chat_job import IndependentChatJob
from app.services.ag_ui_spawn_parent import spawned_parent_public_id
from tests.authz.test_independent_job_authority import delegation as delegation

type Delegation = tuple[OrgContext, ConversationRun, ConversationRun, IndependentChatJob]


def _receipt(context: OrgContext, run: ConversationRun, public_id: str) -> AgUiCommandReceipt:
    return AgUiCommandReceipt(
        tenant_id=context.tenant_id,
        org_id=context.org_id,
        user_id=context.user_id,
        conversation_id=run.conversation_id,
        run_id=run.id,
        public_run_id=public_id,
        request_digest="0" * 64,
        projection_version="1",
    )


def _another_run(original: ConversationRun, source: str) -> ConversationRun:
    return ConversationRun(
        id=uuid4(),
        tenant_id=original.tenant_id,
        org_id=original.org_id,
        user_id=original.user_id,
        conversation_id=original.conversation_id,
        agent_id=original.agent_id,
        run_source="CHAT",
        source=source,
        parent_run_id=original.id if source == "resume" else None,
        status="completed",
        is_active=False,
    )


async def test_initial_child_links_to_current_public_identity_of_its_exact_parent(
    db: AsyncSession, delegation: Delegation
) -> None:
    context, parent, initial, job = delegation
    room = await db.get(Conversation, job.child_conversation_id)
    assert room
    assert await spawned_parent_public_id(db, room, context, initial) == str(parent.id)
    alias = "parent / public?1"
    db.add(_receipt(context, parent, alias))
    await db.commit()
    assert await spawned_parent_public_id(db, room, context, initial) == alias

    unrelated = _another_run(initial, "start")
    db.add(unrelated)
    await db.commit()
    assert await spawned_parent_public_id(db, room, context, unrelated) is None
    assert (
        await spawned_parent_public_id(db, room, replace(context, org_id=uuid4()), initial) is None
    )


async def test_native_parent_uuid_hidden_if_the_public_id_resolves_to_a_different_run(
    db: AsyncSession, delegation: Delegation
) -> None:
    context, parent, initial, job = delegation
    room = await db.get(Conversation, job.child_conversation_id)
    assert room
    other = _another_run(parent, "start")
    db.add(other)
    await db.flush()
    db.add(_receipt(context, other, str(parent.id)))
    await db.commit()
    assert await spawned_parent_public_id(db, room, context, initial) is None


async def test_ordinary_and_noninitial_turns_do_not_query_spawn_history(
    db: AsyncSession, delegation: Delegation
) -> None:
    context, parent, initial, job = delegation
    origin = await db.get(Conversation, job.parent_conversation_id)
    room = await db.get(Conversation, job.child_conversation_id)
    assert room and origin and db.bind

    def unexpected_query(*_: object) -> None:
        pytest.fail("ordinary, edited and resumed turns have no spawn-parent lookup")

    engine = db.bind.sync_engine
    event.listen(engine, "before_cursor_execute", unexpected_query)
    try:
        assert await spawned_parent_public_id(db, origin, context, parent) is None
        for source in ("edit", "regenerate", "resume"):
            assert (
                await spawned_parent_public_id(db, room, context, _another_run(initial, source))
                is None
            )
    finally:
        event.remove(engine, "before_cursor_execute", unexpected_query)


async def test_current_parent_grant_is_required_even_when_child_grant_survives(
    db: AsyncSession, delegation: Delegation, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, parent, initial, job = delegation
    room = await db.get(Conversation, job.child_conversation_id)
    assert room
    actor = f"user:{context.user_id}"
    parent_grant = (actor, "can_run", f"agent:{job.parent_agent_id}")
    child_grant = (actor, "can_run", f"agent:{job.child_agent_id}")
    for grants, expected in (
        ((parent_grant, child_grant), str(parent.id)),
        ((child_grant,), None),
    ):
        enforcer = AuthzService(FakeAuthz(frozenset(grants)), mode="enforce")
        monkeypatch.setattr(runtime, "_current", enforcer)
        assert await spawned_parent_public_id(db, room, context, initial) == expected


async def test_local_delegation_revoked_during_remote_grant_hides_parent(
    db: AsyncSession, delegation: Delegation, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, _, initial, job = delegation
    room = await db.get(Conversation, job.child_conversation_id)
    assert room
    grants = frozenset(
        (f"user:{context.user_id}", "can_run", f"agent:{agent_id}")
        for agent_id in (job.parent_agent_id, job.child_agent_id)
    )
    monkeypatch.setattr(runtime, "_current", AuthzService(FakeAuthz(grants), mode="enforce"))
    batch = FakeAuthz.batch_check
    revoked = False

    async def revoke(
        client: FakeAuthz,
        queries: Sequence[CheckQuery],
        *,
        consistency: Consistency = "MINIMIZE_LATENCY",
    ) -> tuple[bool, ...]:
        nonlocal revoked
        allowed = await batch(client, queries, consistency=consistency)
        if not revoked:
            revoked = True
            async with AsyncSession(db.bind) as writer, writer.begin():
                await writer.execute(
                    delete(AgentSubAgentLink).where(
                        AgentSubAgentLink.parent_agent_id == job.parent_agent_id,
                        AgentSubAgentLink.sub_agent_id == job.child_agent_id,
                    )
                )
        return allowed

    monkeypatch.setattr(FakeAuthz, "batch_check", revoke)
    assert await spawned_parent_public_id(db, room, context, initial) is None
    assert revoked
