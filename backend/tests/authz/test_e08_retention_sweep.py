"""E-08: the per-company ``retention_sweep`` deletes and keeps per period (plan 6.2, 10.7)."""

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.audit_event import AuditEvent
from app.models.conversation import Conversation
from app.models.memory import MemoryRecord
from app.models.organization import OrganizationMember
from app.services import agent_purge_cleanup
from tests.agent_purge_cleanup_cases import RecordingSaver
from tests.agent_purge_service_cases import seed_focused_purge_history
from tests.authz.d04_support import OTHER_ORG_ID
from tests.authz.e01_support import OTHER_USER_ID, THIRD_USER_ID, add_agent
from tests.authz.e02_support import MEMBERS, remove, seed_admin, version
from tests.authz.e08_support import (
    NOW,
    add_conversation,
    add_memory,
    days_ago,
    exists,
    mark_removed,
    set_retention,
    sweep,
)
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed_admin(db)


async def test_conversations_past_the_period_are_deleted_and_recent_ones_kept(
    db: AsyncSession,
) -> None:
    agent = await add_agent(db)
    await set_retention(db, conversation_days=30)
    old = await add_conversation(db, agent, user=TEST_USER_ID, last_activity=days_ago(40))
    recent = await add_conversation(db, agent, user=TEST_USER_ID, last_activity=days_ago(10))
    other_org = await add_conversation(
        db, agent, user=TEST_USER_ID, last_activity=days_ago(400), org=OTHER_ORG_ID
    )
    results = await sweep()
    assert results[TEST_ORG_ID].conversations == 1
    assert not await exists(Conversation, old)
    assert await exists(Conversation, recent)
    assert await exists(Conversation, other_org)  # unlimited by default
    async with TestSession() as session:
        event = await session.scalar(
            select(AuditEvent).where(AuditEvent.action == "retention.purge")
        )
    assert event is not None
    assert (event.target_id, event.actor_type) == (str(TEST_ORG_ID), "system")
    assert event.tenant_id == TEST_TENANT_ID
    assert event.event_metadata == {"conversations": 1}


async def test_default_policy_keeps_conversations_forever(db: AsyncSession) -> None:
    agent = await add_agent(db)
    old = await add_conversation(db, agent, user=TEST_USER_ID, last_activity=days_ago(3000))
    results = await sweep()
    assert results[TEST_ORG_ID].conversations == 0
    assert await exists(Conversation, old)


async def test_removed_member_data_is_kept_inside_and_deleted_after_the_period(
    db: AsyncSession,
) -> None:
    agent = await add_agent(db)
    await set_retention(db, removed_member_days=30)
    leaver = await add_conversation(db, agent, user=OTHER_USER_ID, last_activity=NOW)
    leaver_memory = await add_memory(db, OTHER_USER_ID)
    recent = await add_conversation(db, agent, user=THIRD_USER_ID, last_activity=NOW)
    recent_memory = await add_memory(db, THIRD_USER_ID)
    staying = await add_conversation(db, agent, user=TEST_USER_ID, last_activity=days_ago(90))
    await mark_removed(db, OTHER_USER_ID, days_ago(31))
    await mark_removed(db, THIRD_USER_ID, days_ago(29))
    counts = (await sweep())[TEST_ORG_ID]
    assert (counts.removed_member_conversations, counts.removed_member_memories) == (1, 1)
    assert not await exists(Conversation, leaver)
    assert not await exists(MemoryRecord, leaver_memory)
    assert await exists(Conversation, recent)
    assert await exists(MemoryRecord, recent_memory)
    assert await exists(Conversation, staying)


async def test_re_adding_a_removed_member_restores_their_data(
    client: AsyncClient, db: AsyncSession
) -> None:
    agent = await add_agent(db)
    conversation = await add_conversation(db, agent, user=OTHER_USER_ID, last_activity=NOW)
    memory = await add_memory(db, OTHER_USER_ID)
    removed = await remove(client, {"reason": "left"})
    assert removed.status_code == 200, removed.text
    assert (await sweep())[TEST_ORG_ID].removed_member_conversations == 0  # inside 30 days
    headers = {"If-Match": await version(client)}
    added = await client.post(MEMBERS, json={"user_id": str(OTHER_USER_ID)}, headers=headers)
    assert added.status_code in {200, 201}, added.text
    later = await sweep(NOW + timedelta(days=60))
    assert later[TEST_ORG_ID].removed_member_conversations == 0
    assert await exists(Conversation, conversation)
    assert await exists(MemoryRecord, memory)
    async with TestSession() as session:
        member = await session.get(OrganizationMember, (TEST_ORG_ID, OTHER_USER_ID))
    assert member is not None
    assert (member.status, member.removed_at) == ("active", None)


async def test_agent_past_the_trash_period_is_purged_and_its_conversations_stay_read_only(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = await seed_focused_purge_history()
    assert (await client.delete(f"/api/agents/{history.agent_id}")).status_code == 204
    saver = RecordingSaver()
    monkeypatch.setattr(agent_purge_cleanup.checkpointer, "get_checkpointer", lambda: saver)
    inside = await sweep(NOW + timedelta(days=29), history.saver)
    assert inside[TEST_ORG_ID].agents == 0
    assert await exists(Agent, history.agent_id)
    results = await sweep(NOW + timedelta(days=31), history.saver)
    assert results[TEST_ORG_ID].agents == 1
    assert not await exists(Agent, history.agent_id)
    async with TestSession() as session:
        retained = await session.get(Conversation, history.conversation_id)
    assert retained is not None
    assert (retained.agent_id, retained.agent_lifecycle_state, retained.queue_paused) == (
        None,
        "purged",
        True,
    )
    assert retained.agent_name_snapshot == "History purge"
    detail = await client.get(f"/api/conversations/{history.conversation_id}")
    assert detail.status_code == 200, detail.text


async def test_company_audit_rows_past_the_period_are_deleted(db: AsyncSession) -> None:
    old = AuditEvent(
        tenant_id=TEST_TENANT_ID,
        actor_type="user",
        action="org.update",
        target_type="organization",
        outcome="success",
        created_at=days_ago(366).replace(tzinfo=None),
    )
    recent = AuditEvent(
        tenant_id=TEST_TENANT_ID,
        actor_type="user",
        action="org.update",
        target_type="organization",
        outcome="success",
        created_at=days_ago(300).replace(tzinfo=None),
    )
    platform = AuditEvent(
        actor_type="user",
        action="org.update",
        target_type="organization",
        outcome="success",
        created_at=days_ago(4000).replace(tzinfo=None),
    )
    db.add_all([old, recent, platform])
    await db.commit()
    results = await sweep()
    assert results[TEST_TENANT_ID].audit_events == 1
    assert not await exists(AuditEvent, old.id)
    assert await exists(AuditEvent, recent.id)
    assert await exists(AuditEvent, platform.id)  # no company: never swept here
