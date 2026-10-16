"""E-02 review fixes: member-row locks, operator actor, removed rerun, binding blockers."""

from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState, Session

from app.authz.grants import GrantRequest, GrantService, GrantTarget
from app.authz.org_context import OrgContext
from app.models.audit_event import AuditEvent
from app.models.marketplace import SkillCredentialBinding
from app.models.organization import OrganizationMember
from app.services.user_service import delete_user
from tests.authz.e01_support import (
    OTHER_USER_ID,
    TEST_ORG_ID,
    TEST_USER_ID,
    THIRD_USER_ID,
    add_agent,
    add_credential,
    add_skill,
    add_tool,
    base,
    grants,
)
from tests.authz.e02_support import MEMBERS, delete, remove, seed_admin
from tests.authz.unit_scope_fixture import TEST_TENANT_ID
from tests.conftest import TestSession

CTX = OrgContext(org_id=TEST_ORG_ID, tenant_id=TEST_TENANT_ID, user_id=TEST_USER_ID)


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed_admin(db)


@contextmanager
def row_locks() -> Iterator[list[tuple[str, str]]]:
    """(table, "share"|"update") of every locking ORM SELECT, in order (SQLite drops the
    clause from the SQL text, so the statement objects are inspected)."""
    seen: list[tuple[str, str]] = []

    def hook(state: ORMExecuteState) -> None:
        lock = getattr(state.statement, "_for_update_arg", None)
        if state.is_select and lock is not None:
            froms = state.statement.get_final_froms()  # type: ignore[attr-defined]
            seen.append((getattr(froms[0], "name", ""), "share" if lock.read else "update"))
        elif state.is_update:  # e.g. the org epoch bump (row lock without a SELECT)
            table = getattr(state.statement, "table", None)
            seen.append((getattr(table, "name", ""), "write"))

    event.listen(Session, "do_orm_execute", hook)
    try:
        yield seen
    finally:
        event.remove(Session, "do_orm_execute", hook)


async def _share(db: AsyncSession, tool_id: UUID, user: UUID = OTHER_USER_ID) -> None:
    await GrantService().grant(
        db, CTX, GrantTarget("tool", tool_id), GrantRequest("viewer", "user", user)
    )
    await db.commit()


async def test_share_to_a_user_holds_the_member_row_for_share(db: AsyncSession) -> None:
    tool = await add_tool(db)
    with row_locks() as seen:
        await _share(db, tool.id)
    assert seen[0] == ("organization_members", "share")  # before the grant objects


async def test_transfer_locks_the_org_row_before_the_new_owners_member_row(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool = await add_tool(db)
    with row_locks() as seen:
        body = {"new_owner_id": str(THIRD_USER_ID)}
        response = await client.post(f"{base('tool', tool.id)}/transfer", json=body)
    assert response.status_code == 200, response.text
    tables = [table for table, _ in seen]
    assert tables.index("organizations") < tables.index("organization_members")
    assert ("organization_members", "share") in seen


async def test_erasure_locks_scopes_before_revoking_and_audits_the_operator(
    db: AsyncSession,
) -> None:
    tool = await add_tool(db)
    await _share(db, tool.id)
    with row_locks() as seen, patch("app.agent_runtime.checkpointer.delete_thread", AsyncMock()):
        await delete_user(db, OTHER_USER_ID, operator_id=TEST_USER_ID)
    await db.commit()
    tables = [table for table, _ in seen]
    first_grant_lock = tables.index("resource_grants")
    assert tables.index("tenants") < tables.index("organizations") < first_grant_lock
    assert tables.index("organization_members") < first_grant_lock
    async with TestSession() as session:
        actor = await session.scalar(
            select(AuditEvent.actor_user_id).where(
                AuditEvent.action == "grant.revoke", AuditEvent.target_id == str(tool.id)
            )
        )
    assert actor == TEST_USER_ID


async def test_rerunning_removal_of_a_removed_member_revokes_left_over_grants(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool = await add_tool(db)
    await _share(db, tool.id)
    await db.execute(  # a removal from before E-02 left the grant active
        update(OrganizationMember)
        .where(
            OrganizationMember.org_id == TEST_ORG_ID,
            OrganizationMember.user_id == OTHER_USER_ID,
        )
        .values(status="removed")
    )
    await db.commit()
    assert (await delete(client)).status_code == 204
    assert [g.state for g in await grants(tool.id)] == ["revoking"]
    again = await remove(client, {"reason": "cleanup"})
    assert again.status_code == 200 and again.json()["revoked_grant_ids"] == []


async def test_a_skill_binding_blocks_credential_delete_and_shows_in_the_preview(
    client: AsyncClient, db: AsyncSession
) -> None:
    credential = await add_credential(db, owner=OTHER_USER_ID)
    skill = await add_skill(db, owner=TEST_USER_ID, slug="bound")
    binding = SkillCredentialBinding(
        skill_id=skill.id, user_id=OTHER_USER_ID, requirement_key="k", credential_id=credential.id
    )
    db.add(binding)
    await db.commit()
    preview = await client.get(f"{MEMBERS}/{OTHER_USER_ID}/exit-preview")
    [owned] = preview.json()["owned"]
    assert owned["blocked_by"] == {"skill_bindings": [str(binding.id)]}
    item = {"resource_type": "credential", "resource_id": str(credential.id), "action": "delete"}
    response = await remove(client, {"reason": "left", "resolutions": [item]})
    assert response.status_code == 409
    error = response.json()["error"]
    assert (error["code"], error["details"]) == (
        "transfer_blocked",
        {"skill_bindings": [str(binding.id)]},
    )


def _first(seen: list[tuple[str, str]], table: str) -> int:
    return [name for name, _ in seen].index(table)


async def test_share_level_and_expiry_changes_lock_the_org_before_the_member_row(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool = await add_tool(db)
    path = f"{base('tool', tool.id)}/grants"
    principal = {"type": "user", "id": str(OTHER_USER_ID)}
    assert (
        await client.post(path, json={"principal": principal, "level": "viewer"})
    ).status_code == 201
    with row_locks() as seen:  # level change: revoke (org epoch) + create (member row)
        changed = await client.post(path, json={"principal": principal, "level": "editor"})
    assert changed.status_code == 201, changed.text
    assert seen[0] == ("organizations", "update")
    assert _first(seen, "organizations") < _first(seen, "organization_members")
    agent = await add_agent(db)
    path = f"{base('agent', agent.id)}/grants"
    first = {"principal": principal, "level": "consumer", "expires_at": "2999-01-01T00:00:00Z"}
    assert (await client.post(path, json=first)).status_code == 201
    with row_locks() as seen:  # expiry change in place: marker (org epoch) after member row
        moved = await client.post(path, json={**first, "expires_at": "2998-01-01T00:00:00Z"})
    assert moved.status_code in (200, 201), moved.text
    assert seen[0] == ("organizations", "update")
    assert _first(seen, "organization_members") < _first(seen, "resource_grants")


async def test_revoke_route_order_is_org_then_grants_then_resource(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool = await add_tool(db)
    body = {"principal": {"type": "user", "id": str(OTHER_USER_ID)}, "level": "viewer"}
    grant_id = (await client.post(f"{base('tool', tool.id)}/grants", json=body)).json()["grant_id"]
    with row_locks() as seen:
        assert (await client.delete(f"/api/grants/{grant_id}")).status_code == 204
    tables = [name for name, _ in seen]
    assert "organization_members" not in tables
    assert _first(seen, "organizations") < _first(seen, "resource_grants") < _first(seen, "tools")
