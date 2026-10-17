"""E-08: retention policy routes; shortening needs preview + confirmation (plan 9.7, S05, S08)."""

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import CurrentUser
from app.models.audit_event import AuditEvent
from app.models.organization import Organization
from app.schemas.retention import org_retention
from tests.authz.e01_support import OTHER_USER_ID, add_agent
from tests.authz.e02_support import seed_admin
from tests.authz.e08_support import RETENTION, add_conversation, days_ago
from tests.authz.e09_support import TENANT, acting_as
from tests.authz.e09_support import make_admin as make_company_admin
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession

POLICIES = f"{TENANT}/policies"
APPROVAL = {"marketplace_tenant_publish": "approval"}
DEFAULTS = {"conversation_days": None, "agent_trash_days": 30, "removed_member_days": 30}


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed_admin(db)


async def _put(client: AsyncClient, body: dict[str, object], url: str = RETENTION):
    etag = (await client.get(url)).headers["ETag"]
    return await client.put(url, json=body, headers={"If-Match": etag})


async def _events(action: str) -> list[AuditEvent]:
    async with TestSession() as db:
        return list(await db.scalars(select(AuditEvent).where(AuditEvent.action == action)))


async def test_defaults_are_unlimited_conversations_and_30_day_periods(
    client: AsyncClient,
) -> None:
    response = await client.get(RETENTION)
    assert response.status_code == 200, response.text
    body = response.json()
    assert {name: body[name] for name in DEFAULTS} == DEFAULTS
    assert response.headers["ETag"]


async def test_member_without_policy_permission_cannot_update(
    client: AsyncClient, test_app: FastAPI
) -> None:
    member = CurrentUser(id=OTHER_USER_ID, email="o@t.io", name="Other", is_super_user=False)
    with acting_as(test_app, member, TEST_ORG_ID):
        assert (await client.get(RETENTION)).status_code == 200
        response = await _put(client, {**DEFAULTS, "agent_trash_days": 60})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_required"


async def test_lengthening_saves_without_confirmation_and_is_audited(
    client: AsyncClient,
) -> None:
    response = await _put(client, {**DEFAULTS, "agent_trash_days": 60})
    assert response.status_code == 200, response.text
    assert response.json()["saved"] is True
    assert response.json()["policy"]["agent_trash_days"] == 60
    [event] = await _events("policy.retention_update")
    assert event.event_metadata is not None
    assert event.event_metadata["before"]["agent_trash_days"] == 30
    assert event.event_metadata["after"]["agent_trash_days"] == 60


async def test_shortening_previews_deletions_then_saves_on_confirm(
    client: AsyncClient, db: AsyncSession
) -> None:
    agent = await add_agent(db)
    for age in (40, 50, 5):
        await add_conversation(db, agent, user=TEST_USER_ID, last_activity=days_ago(age))
    body = {**DEFAULTS, "conversation_days": 30}
    preview = await _put(client, body)
    assert preview.status_code == 200, preview.text
    assert preview.json()["saved"] is False
    assert preview.json()["preview"]["conversations"] == 2
    assert preview.json()["policy"]["conversation_days"] is None  # nothing stored
    assert await _events("policy.retention_update") == []
    confirmed = await _put(client, {**body, "confirm": True})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["saved"] is True
    assert (await client.get(RETENTION)).json()["conversation_days"] == 30
    async with TestSession() as session:
        org = await session.get(Organization, TEST_ORG_ID)
    assert org is not None and org_retention(org.settings).conversation_days == 30


async def test_out_of_range_periods_are_422(client: AsyncClient) -> None:
    for field, value in (("conversation_days", 10), ("agent_trash_days", 91)):
        response = await _put(client, {**DEFAULTS, field: value})
        assert response.status_code == 422, field


async def test_company_audit_retention_shortening_needs_confirmation(
    client: AsyncClient, db: AsyncSession
) -> None:
    await make_company_admin(db)
    assert (await client.get(POLICIES)).json()["audit_retention_days"] == 365
    db.add(
        AuditEvent(
            tenant_id=TEST_TENANT_ID,
            actor_type="user",
            action="org.update",
            target_type="organization",
            outcome="success",
            created_at=days_ago(120).replace(tzinfo=None),
        )
    )
    await db.commit()
    preview = await _put(client, {**APPROVAL, "audit_retention_days": 90}, POLICIES)
    assert preview.status_code == 200, preview.text
    assert preview.json()["preview"] == {
        "conversations": 0,
        "agents": 0,
        "removed_member_conversations": 0,
        "removed_member_memories": 0,
        "audit_events": 1,
    }
    assert preview.json()["audit_retention_days"] == 365
    confirmed = await _put(
        client, {**APPROVAL, "audit_retention_days": 90, "confirm": True}, POLICIES
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["audit_retention_days"] == 90
    assert confirmed.json()["preview"] is None
    [event] = await _events("policy.retention_update")
    assert event.event_metadata is not None
    assert (event.event_metadata["before"], event.event_metadata["after"]) == (365, 90)
    assert event.tenant_id == TEST_TENANT_ID


async def test_trash_deadline_is_exposed_only_when_the_sweep_is_on(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = await add_agent(db)
    assert (await client.delete(f"/api/agents/{agent.id}")).status_code == 204

    async def deadline() -> object:
        listed = (await client.get("/api/agents/archived")).json()
        return next(item for item in listed if item["agent_id"] == str(agent.id))[
            "policy_delete_at"
        ]

    assert await deadline() is None
    monkeypatch.setattr(settings, "retention_sweep_enabled", True)
    assert await deadline() is not None
