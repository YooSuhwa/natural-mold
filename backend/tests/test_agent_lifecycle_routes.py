from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import event, select

from app.models.agent import Agent
from app.models.agent_api import AgentDeployment
from app.models.agent_subagent import AgentSubAgentLink
from app.models.agent_trigger import AgentTrigger
from app.models.audit_event import AuditEvent
from app.models.conversation import Conversation
from app.models.conversation_run_input import ConversationRunInput
from app.models.organization import (
    OrganizationMember,
    OrganizationRoleGrant,
    TenantMember,
)
from app.models.share_link import ShareLink
from app.models.user import User
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession, engine, seed_agent

METADATA_KEYS = {
    "agent_id",
    "name",
    "owner_user_id",
    "owner_label",
    "archived_by_user_id",
    "archived_at",
    "lifecycle_state",
    "policy_delete_at",
    "can_archive",
    "can_restore",
    "can_purge",
}


async def _seed_restorable_graph() -> tuple:
    async with TestSession() as db:
        user, model, agent = await seed_agent(db)
        child = Agent(
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            user_id=user.id,
            name="Child",
            system_prompt="child",
            model_id=model.id,
        )
        db.add(child)
        await db.flush()
        db.add(AgentSubAgentLink(parent_agent_id=agent.id, sub_agent_id=child.id, position=0))
        conversation = Conversation(
            agent_id=agent.id,
            title="Lifecycle",
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            user_id=TEST_USER_ID,
            agent_name_snapshot=agent.name,
            last_activity_at=datetime.now(UTC),
        )
        db.add(conversation)
        await db.flush()
        pending = ConversationRunInput(
            conversation_id=conversation.id,
            agent_id=agent.id,
            user_id=TEST_USER_ID,
            client_request_id=f"lifecycle-{uuid4()}",
            source="ui",
            status="pending",
            priority=0,
            position=1,
            revision=1,
            input_payload={"message": "pending"},
            attachment_ids=[],
        )
        share = ShareLink(
            share_token=uuid4().hex,
            conversation_id=conversation.id,
            created_by=TEST_USER_ID,
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
        )
        trigger = AgentTrigger(
            agent_id=agent.id,
            user_id=TEST_USER_ID,
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            name="Lifecycle trigger",
            trigger_type="interval",
            schedule_config={"minutes": 5},
            input_message="run",
            timezone="Asia/Seoul",
            status="active",
        )
        deployment = AgentDeployment(
            agent_id=agent.id,
            user_id=TEST_USER_ID,
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            public_id=f"lifecycle-{uuid4().hex}",
            status="active",
        )
        db.add_all([pending, share, trigger, deployment])
        await db.commit()
        return agent.id, child.id, conversation.id, pending.id, share.id, trigger.id, deployment.id


async def test_archive_is_idempotent_and_archived_list_is_metadata_only(
    client: AsyncClient,
) -> None:
    # Given: one active Agent with pending queue work.
    agent_id, _, _, input_id, _, _, _ = await _seed_restorable_graph()

    # When: compatibility DELETE is repeated.
    first = await client.delete(f"/api/agents/{agent_id}")
    second = await client.delete(f"/api/agents/{agent_id}")
    archived = await client.get("/api/agents/archived")

    # Then: the input is canceled once and governance exposes metadata only.
    assert first.status_code == second.status_code == 204
    assert archived.status_code == 200, archived.text
    item = next(row for row in archived.json() if row["agent_id"] == str(agent_id))
    assert set(item) == METADATA_KEYS
    assert item["lifecycle_state"] == "archived_stopped"
    async with TestSession() as db:
        pending = await db.get(ConversationRunInput, input_id)
        assert pending is not None
        assert (pending.status, pending.run_id, pending.revision) == ("canceled", None, 2)
        actions = tuple(
            await db.scalars(
                select(AuditEvent.action).where(
                    AuditEvent.target_id == str(agent_id), AuditEvent.action == "agent.archive"
                )
            )
        )
        assert actions == ("agent.archive",)


async def test_restore_keeps_revoked_and_disabled_resources_inactive(
    client: AsyncClient,
) -> None:
    # Given: archive has revoked every current entry point and is fully stopped.
    (
        agent_id,
        child_id,
        conversation_id,
        _,
        share_id,
        trigger_id,
        deployment_id,
    ) = await _seed_restorable_graph()
    archived = await client.delete(f"/api/agents/{agent_id}")
    assert archived.status_code == 204

    # When: the owner restores twice.
    first = await client.post(f"/api/agents/{agent_id}/restore")
    second = await client.post(f"/api/agents/{agent_id}/restore")

    # Then: execution metadata returns active without reviving revoked resources.
    assert first.status_code == second.status_code == 200
    assert first.json()["lifecycle_state"] == "active"
    async with TestSession() as db:
        agent = await db.get(Agent, agent_id)
        conversation = await db.get(Conversation, conversation_id)
        share = await db.get(ShareLink, share_id)
        trigger = await db.get(AgentTrigger, trigger_id)
        deployment = await db.get(AgentDeployment, deployment_id)
        assert agent is not None and agent.archived_at is None
        assert conversation is not None
        assert (conversation.agent_lifecycle_state, conversation.queue_paused) == ("active", False)
        assert share is not None and share.revoked_at is not None
        assert trigger is not None and trigger.status == "paused"
        assert deployment is not None and deployment.status == "disabled"
        link = await db.scalar(
            select(AgentSubAgentLink).where(
                AgentSubAgentLink.parent_agent_id == agent_id,
                AgentSubAgentLink.sub_agent_id == child_id,
            )
        )
        assert link is None


async def test_nonowner_governance_routes_never_select_private_agent_payload(
    client: AsyncClient,
) -> None:
    # Given: a persisted organization administrator governing another user's Agent.
    agent_id, _, _, _, _, _, _ = await _seed_restorable_graph()
    async with TestSession() as db:
        owner = User(email="lifecycle-owner@example.test", name="Lifecycle owner")
        db.add(owner)
        await db.flush()
        db.add_all(
            [
                TenantMember(tenant_id=TEST_TENANT_ID, user_id=owner.id),
                OrganizationMember(
                    org_id=TEST_ORG_ID,
                    tenant_id=TEST_TENANT_ID,
                    user_id=owner.id,
                ),
                OrganizationRoleGrant(
                    org_id=TEST_ORG_ID,
                    tenant_id=TEST_TENANT_ID,
                    role="admin",
                    principal_type="user",
                    principal_id=TEST_USER_ID,
                ),
            ]
        )
        agent = await db.get(Agent, agent_id)
        assert agent is not None
        agent.user_id = owner.id
        await db.commit()

    statements: list[str] = []

    def capture_statement(_conn, _cursor, statement, _parameters, _context, _many) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", capture_statement)
    try:
        archived = await client.delete(f"/api/agents/{agent_id}")
        restored = await client.post(f"/api/agents/{agent_id}/restore")
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture_statement)

    assert archived.status_code == 204
    assert restored.status_code == 200
    normalized_statements = [" ".join(statement.lower().split()) for statement in statements]
    agent_selects = [
        statement
        for statement in normalized_statements
        if statement.startswith("select") and " from agents" in statement
    ]
    assert agent_selects
    assert all(
        private_column not in statement
        for statement in agent_selects
        for private_column in (
            "agents.description",
            "agents.system_prompt",
            "agents.model_id",
            "agents.llm_credential_id",
            "agents.model_params",
            "agents.middleware_configs",
            "agents.runtime_policy",
            "agents.opener_questions",
            "agents.model_fallback_list",
            "agents.image_path",
            "agents.template_id",
        )
    )
