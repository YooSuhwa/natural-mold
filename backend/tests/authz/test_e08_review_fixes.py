"""E-08 review fixes: lock re-checks, idle definition, chunking, cleanup order, purge actor."""

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_api import AgentApiRun
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.audit_event import AuditEvent
from app.models.conversation import Conversation
from app.models.organization import OrganizationMember, OrganizationRoleGrant
from app.models.user import User
from app.services import agent_purge_cleanup
from app.services.retention import conversations as retention_conversations
from app.services.retention import sweep as retention_sweep
from app.services.retention import targets
from app.services.retention.agent_trash import purge_actor
from app.services.retention.sweep import OrgRef
from tests.agent_purge_cleanup_cases import RecordingSaver
from tests.agent_purge_service_cases import seed_focused_purge_history
from tests.authz.e01_support import OTHER_USER_ID, add_agent
from tests.authz.e02_support import seed_admin
from tests.authz.e08_support import (
    NOW,
    add_conversation,
    days_ago,
    exists,
    mark_removed,
    set_retention,
    sweep,
)
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession

ORG = OrgRef(TEST_ORG_ID, TEST_TENANT_ID, "org", {})


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed_admin(db)


async def test_activity_after_listing_keeps_the_conversation(db: AsyncSession) -> None:
    agent = await add_agent(db)
    conversation = await add_conversation(db, agent, user=TEST_USER_ID, last_activity=days_ago(40))
    idle = targets.expired_conversations(ORG, 30, NOW)
    listed = list(await db.scalars(idle))
    assert listed == [conversation]
    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation)
        .values(last_activity_at=NOW, updated_at=NOW.replace(tzinfo=None))
    )
    await db.commit()
    assert await retention_conversations.delete_conversations(db, listed, idle) == 0
    assert await exists(Conversation, conversation)


async def test_schedule_and_agent_api_activity_keep_an_old_conversation(
    db: AsyncSession,
) -> None:
    agent = await add_agent(db)
    await set_retention(db, conversation_days=30)
    scheduled = await add_conversation(db, agent, user=TEST_USER_ID, last_activity=days_ago(400))
    api = await add_conversation(db, agent, user=TEST_USER_ID, last_activity=days_ago(400))
    idle = await add_conversation(db, agent, user=TEST_USER_ID, last_activity=days_ago(400))
    db.add(
        AgentTriggerRun(
            trigger_name_snapshot="daily",
            user_id=TEST_USER_ID,
            conversation_id=scheduled,
            input_message="run",
            created_at=days_ago(1).replace(tzinfo=None),
        )
    )
    db.add(
        AgentApiRun(
            public_id="run-e08",
            user_id=TEST_USER_ID,
            conversation_id=api,
            deployment_public_id_snapshot="dep",
            mode="sync",
            created_at=days_ago(1).replace(tzinfo=None),
        )
    )
    await db.commit()
    counts = (await sweep())[TEST_ORG_ID]
    assert counts.conversations == 1
    assert await exists(Conversation, scheduled)
    assert await exists(Conversation, api)
    assert not await exists(Conversation, idle)


async def test_re_add_during_the_sweep_keeps_the_member_conversation(db: AsyncSession) -> None:
    agent = await add_agent(db)
    conversation = await add_conversation(db, agent, user=OTHER_USER_ID, last_activity=NOW)
    await mark_removed(db, OTHER_USER_ID, days_ago(40))
    owned = targets.member_conversations(ORG, targets.expired_members(ORG, 30, NOW))
    listed = list(await db.scalars(owned))
    assert listed == [conversation]
    member = await db.get(OrganizationMember, (TEST_ORG_ID, OTHER_USER_ID))
    assert member is not None
    member.status, member.removed_at = "active", None
    await db.commit()
    prelock = retention_sweep._member_prelock(ORG)  # noqa: SLF001 - the sweep's lock order
    deleted = await retention_conversations.delete_conversations(db, listed, owned, prelock)
    assert deleted == 0
    assert await exists(Conversation, conversation)


async def test_audit_rows_are_deleted_in_bounded_chunks(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(retention_sweep, "CHUNK", 2)
    for _ in range(5):
        db.add(
            AuditEvent(
                tenant_id=TEST_TENANT_ID,
                actor_type="user",
                action="org.update",
                target_type="organization",
                outcome="success",
                created_at=days_ago(400).replace(tzinfo=None),
            )
        )
    await db.commit()
    assert (await sweep())[TEST_TENANT_ID].audit_events == 5


async def test_file_cleanup_failure_does_not_undo_the_committed_delete(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = await add_agent(db)
    await set_retention(db, conversation_days=30)
    first = await add_conversation(db, agent, user=TEST_USER_ID, last_activity=days_ago(40))
    second = await add_conversation(db, agent, user=TEST_USER_ID, last_activity=days_ago(50))

    async def broken(_thread_id: str) -> None:
        raise RuntimeError("checkpointer down")

    monkeypatch.setattr(retention_conversations, "delete_thread", broken)
    assert (await sweep())[TEST_ORG_ID].conversations == 2
    assert not await exists(Conversation, first)
    assert not await exists(Conversation, second)


async def test_sweep_purge_is_marked_as_system_in_the_purge_audit(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = await seed_focused_purge_history()
    assert (await client.delete(f"/api/agents/{history.agent_id}")).status_code == 204
    saver = RecordingSaver()
    monkeypatch.setattr(agent_purge_cleanup.checkpointer, "get_checkpointer", lambda: saver)
    assert (await sweep(NOW + timedelta(days=31), history.saver))[TEST_ORG_ID].agents == 1
    async with TestSession() as session:
        event = await session.scalar(select(AuditEvent).where(AuditEvent.action == "agent.purge"))
    assert event is not None and event.event_metadata is not None
    assert event.event_metadata["retention_sweep"] is True
    assert event.event_metadata["actor_kind"] == "system"


async def test_org_without_administrator_keeps_trash_and_records_a_warning(
    client: AsyncClient, db: AsyncSession
) -> None:
    agent = await add_agent(db)
    assert (await client.delete(f"/api/agents/{agent.id}")).status_code == 204
    await db.execute(
        delete(OrganizationRoleGrant).where(
            OrganizationRoleGrant.org_id == TEST_ORG_ID, OrganizationRoleGrant.role == "admin"
        )
    )
    await db.commit()
    assert (await sweep(NOW + timedelta(days=31)))[TEST_ORG_ID].agents == 0
    async with TestSession() as session:
        event = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "retention.purge", AuditEvent.outcome == "skipped"
            )
        )
    assert event is not None and event.reason_code == "no_org_administrator"


async def test_borrowed_administrator_is_never_a_super_user(db: AsyncSession) -> None:
    admin = await db.get(User, TEST_USER_ID)
    assert admin is not None
    admin.is_super_user = True
    await db.commit()
    actor = await purge_actor(db, TEST_ORG_ID)
    assert actor is not None and actor.is_super_user is False


async def test_leadership_loss_stops_before_the_next_organization(db: AsyncSession) -> None:
    agent = await add_agent(db)
    await set_retention(db, conversation_days=30)
    old = await add_conversation(db, agent, user=TEST_USER_ID, last_activity=days_ago(40))

    async def lost() -> bool:
        return False

    from app.services.retention.job import run_retention_sweep

    results = await run_retention_sweep(maker=TestSession, now=NOW, still_leader=lost)
    assert results[TEST_TENANT_ID] == {}
    assert await exists(Conversation, old)
