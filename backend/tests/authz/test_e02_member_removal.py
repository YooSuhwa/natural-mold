"""E-02: member removal with forced settlement (plan 10.7 "구성원 제거", S15)."""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_api import AgentApiKey, AgentDeployment
from app.models.agent_trigger import AgentTrigger
from app.models.audit_event import AuditEvent
from app.models.credential import Credential
from app.models.organization import OrganizationMember
from app.models.tool import Tool
from app.models.user import User
from tests.authz.e01_support import (
    FOREIGN_USER_ID,
    OTHER_USER_ID,
    TEST_ORG_ID,
    TEST_USER_ID,
    THIRD_USER_ID,
    add_agent,
    add_credential,
    add_group,
    add_tool,
    audit_event,
    base,
    grants,
)
from tests.authz.e02_support import MEMBERS, add_entry_points, delete, remove, seed_admin
from tests.conftest import TestSession


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed_admin(db)


async def _member_status() -> str:
    async with TestSession() as db:
        member = await db.scalar(
            select(OrganizationMember).where(
                OrganizationMember.org_id == TEST_ORG_ID,
                OrganizationMember.user_id == OTHER_USER_ID,
            )
        )
        assert member is not None
        return member.status


async def _get(model: type, row_id: object) -> object:
    async with TestSession() as db:
        return await db.get(model, row_id)


def _transfer(kind: str, row_id: object) -> dict[str, str]:
    return {"resource_type": kind, "resource_id": str(row_id), "new_owner_id": str(THIRD_USER_ID)}


async def test_preview_lists_what_the_removal_settles_and_stops(
    client: AsyncClient, db: AsyncSession
) -> None:
    agent = await add_agent(db, owner=OTHER_USER_ID)
    trigger, deployment, key = await add_entry_points(db, agent)
    shared = await add_tool(db)
    share = {"principal": {"type": "user", "id": str(OTHER_USER_ID)}, "level": "viewer"}
    assert (await client.post(f"{base('tool', shared.id)}/grants", json=share)).status_code == 201
    group = await add_group(db, members=(OTHER_USER_ID,))
    response = await client.get(f"{MEMBERS}/{OTHER_USER_ID}/exit-preview")
    assert response.status_code == 200, response.text
    body = response.json()
    assert [(o["resource_type"], o["resource_id"]) for o in body["owned"]] == [
        ("agent", str(agent.id))
    ]
    assert body["owned"][0]["blocked_by"] is None  # the leaver's own entry points stop
    assert [g["resource_id"] for g in body["grants"]] == [str(shared.id)]
    assert body["group_ids"] == [str(group)]
    assert (body["trigger_ids"], body["deployment_ids"], body["api_key_ids"]) == (
        [str(trigger.id)],
        [str(deployment.id)],
        [str(key.id)],
    )
    assert body["is_last_admin"] is False
    assert await _member_status() == "active"  # read only


async def test_owned_resources_block_removal_and_are_listed(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool = await add_tool(db, owner=OTHER_USER_ID)
    for response in (await delete(client), await remove(client, {"reason": "left"})):
        assert response.status_code == 409
        error = response.json()["error"]
        assert error["code"] == "resource_transfer_required"
        assert error["details"] == {"resources": {"tool": [str(tool.id)]}}
    assert await _member_status() == "active"


async def test_removal_transfers_stops_entry_points_and_revokes_grants(
    client: AsyncClient, db: AsyncSession
) -> None:
    credential = await add_credential(db, owner=OTHER_USER_ID)
    agent = await add_agent(db, owner=OTHER_USER_ID)
    agent_row = await db.get(Agent, agent.id)
    assert agent_row is not None
    agent_row.llm_credential_id = credential.id  # moves with the agent: credentials first
    await db.commit()
    trigger, deployment, key = await add_entry_points(db, agent)
    shared = await add_tool(db)
    share = {"principal": {"type": "user", "id": str(OTHER_USER_ID)}, "level": "editor"}
    assert (await client.post(f"{base('tool', shared.id)}/grants", json=share)).status_code == 201
    await add_group(db, members=(OTHER_USER_ID,))
    body = {
        "reason": "퇴사",
        "resolutions": [_transfer("agent", agent.id), _transfer("credential", credential.id)],
    }
    response = await remove(client, body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert set(result["transferred"]) == {str(agent.id), str(credential.id)}
    assert len(result["revoked_grant_ids"]) == 1
    assert await _member_status() == "removed"
    for model, row_id in ((Agent, agent.id), (Credential, credential.id)):
        moved = await _get(model, row_id)
        assert moved is not None and moved.user_id == THIRD_USER_ID  # type: ignore[attr-defined]
    stopped_trigger = await _get(AgentTrigger, trigger.id)
    stopped_deployment = await _get(AgentDeployment, deployment.id)
    revoked_key = await _get(AgentApiKey, key.id)
    assert stopped_trigger.status == "paused"  # type: ignore[attr-defined]
    assert stopped_deployment.status == "disabled"  # type: ignore[attr-defined]
    assert revoked_key.revoked_at is not None  # type: ignore[attr-defined]
    assert [g.state for g in await grants(shared.id)] == ["revoking"]  # until OpenFGA applies
    revoke = await audit_event(shared.id, "grant.revoke")
    assert revoke.event_metadata is not None and revoke.event_metadata["member_exit"] is True
    transfer = await audit_event(agent.id, "ownership.transfer")
    assert transfer.event_metadata is not None
    assert (transfer.event_metadata["governance"], transfer.event_metadata["reason"]) == (
        True,
        "퇴사",
    )
    exit_event = await audit_event(OTHER_USER_ID, "org.member_remove")
    assert exit_event.event_metadata is not None
    assert exit_event.event_metadata["revoked_grants"] == 1
    assert set(exit_event.event_metadata["transferred"]) == {str(agent.id), str(credential.id)}


async def test_personal_account_credential_is_deleted_not_transferred(
    client: AsyncClient, db: AsyncSession
) -> None:
    gmail = await add_credential(db, owner=OTHER_USER_ID, definition_key="google_workspace_oauth2")
    refused = await remove(
        client, {"reason": "left", "resolutions": [_transfer("credential", gmail.id)]}
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "transfer_blocked"
    assert await _member_status() == "active" and await _get(Credential, gmail.id) is not None
    item = {"resource_type": "credential", "resource_id": str(gmail.id), "action": "delete"}
    deleted = await remove(client, {"reason": "left", "resolutions": [item]})
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] == [str(gmail.id)]
    assert await _get(Credential, gmail.id) is None


async def test_delete_action_is_credentials_only(client: AsyncClient, db: AsyncSession) -> None:
    tool = await add_tool(db, owner=OTHER_USER_ID)
    item = {"resource_type": "tool", "resource_id": str(tool.id), "action": "delete"}
    response = await remove(client, {"reason": "left", "resolutions": [item]})
    assert response.status_code == 422
    assert response.json()["error"]["details"] == {"reason": "delete_unsupported"}


async def test_archived_agent_stays_with_its_owner_and_does_not_block(
    client: AsyncClient, db: AsyncSession
) -> None:
    agent = await add_agent(db, owner=OTHER_USER_ID)
    row = await db.get(Agent, agent.id)
    assert row is not None
    row.archived_at = datetime.now(UTC).replace(tzinfo=None)
    await db.commit()
    assert (await delete(client)).status_code == 204
    kept = await _get(Agent, agent.id)
    assert kept is not None and kept.user_id == OTHER_USER_ID  # type: ignore[attr-defined]


async def test_governor_cannot_take_ownership_and_nothing_is_written(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool = await add_tool(db, owner=OTHER_USER_ID)
    item = {**_transfer("tool", tool.id), "new_owner_id": str(TEST_USER_ID)}
    response = await remove(client, {"reason": "left", "resolutions": [item]})
    assert response.status_code == 422
    assert response.json()["error"]["details"] == {"reason": "self_transfer"}
    moved = await _get(Tool, tool.id)
    assert moved.user_id == OTHER_USER_ID  # type: ignore[attr-defined]
    assert await _member_status() == "active"


async def test_resolution_must_name_a_resource_the_leaver_owns_and_a_member(
    client: AsyncClient, db: AsyncSession
) -> None:
    mine = await add_tool(db)
    theirs = await add_tool(db, owner=OTHER_USER_ID)
    not_owned = await remove(client, {"reason": "x", "resolutions": [_transfer("tool", mine.id)]})
    assert not_owned.status_code == 422
    assert not_owned.json()["error"]["details"] == {"reason": "not_owned"}
    item = {**_transfer("tool", theirs.id), "new_owner_id": str(FOREIGN_USER_ID)}
    outsider = await remove(client, {"reason": "x", "resolutions": [item]})
    assert outsider.status_code == 422  # the new owner must be an active member (D21)
    assert (await _get(Tool, theirs.id)).user_id == OTHER_USER_ID  # type: ignore[union-attr]
    assert await _member_status() == "active"


async def test_another_users_trigger_on_the_leavers_agent_blocks_its_transfer(
    client: AsyncClient, db: AsyncSession
) -> None:
    agent = await add_agent(db, owner=OTHER_USER_ID)
    await add_entry_points(db, agent, owner=THIRD_USER_ID)
    response = await remove(client, {"reason": "x", "resolutions": [_transfer("agent", agent.id)]})
    assert response.status_code == 409
    details = response.json()["error"]["details"]
    assert response.json()["error"]["code"] == "transfer_blocked"
    assert set(details) == {"triggers", "deployments", "api_keys"}


async def test_removal_is_idempotent_and_clears_the_saved_organization(
    client: AsyncClient, db: AsyncSession
) -> None:
    user = await db.get(User, OTHER_USER_ID)
    assert user is not None
    user.last_active_org_id = TEST_ORG_ID
    await db.commit()
    first = await remove(client, {"reason": "left"})
    assert first.status_code == 200, first.text
    async with TestSession() as session:
        saved = await session.scalar(
            select(User.last_active_org_id).where(User.id == OTHER_USER_ID)
        )
        before = len(list(await session.scalars(select(AuditEvent.id))))
    assert saved is None
    again = await remove(client, {"reason": "left"})
    assert again.status_code == 200 and again.json()["revoked_grant_ids"] == []
    assert (await delete(client)).status_code == 204
    async with TestSession() as session:
        assert len(list(await session.scalars(select(AuditEvent.id)))) == before
