"""Every current-Etag self mutation preserves the final active company admin."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from httpx import Response
from sqlalchemy import func, select

from app.models.audit_event import AuditEvent
from app.models.organization import TenantMember, TenantRoleGrant

if TYPE_CHECKING:
    from tests.authz.tenant_members_fixture import MemberSession, TenantMembersFixture

pytest_plugins = ["tests.authz.tenant_members_fixture"]
Operation = Literal["demote", "clear", "suspend", "delete"]


async def attempt(
    fixture: TenantMembersFixture,
    session: MemberSession,
    version: str,
    operation: Operation,
) -> Response:
    path = f"/api/tenants/{fixture.tenant.id}"
    match operation:
        case "demote":
            return await fixture.client.put(
                f"{path}/roles/{fixture.admin.id}",
                json={"roles": ["auditor"]},
                headers=session.headers(version),
            )
        case "clear":
            return await fixture.client.put(
                f"{path}/roles/{fixture.admin.id}",
                json={"roles": []},
                headers=session.headers(version),
            )
        case "suspend":
            return await fixture.client.patch(
                f"{path}/members/{fixture.admin.id}",
                json={"status": "suspended"},
                headers=session.headers(version),
            )
        case "delete":
            return await fixture.client.delete(
                f"{path}/members/{fixture.admin.id}", headers=session.headers(version)
            )
        case _ as unreachable:
            assert_never(unreachable)


@pytest.mark.parametrize("operation", ["demote", "clear", "suspend", "delete"])
async def test_current_etag_rejects_every_last_admin_self_mutation_without_writes(
    tenant_members_fixture: TenantMembersFixture,
    operation: Operation,
) -> None:
    fixture = tenant_members_fixture
    session = await fixture.login(fixture.admin)
    peer = await fixture.client.patch(
        f"/api/tenants/{fixture.tenant.id}/members/{fixture.peer_admin.id}",
        json={"status": "suspended"},
        headers=session.headers(await fixture.version()),
    )
    assert peer.status_code == 200
    version = peer.headers["etag"]
    audit_before = await fixture.db.scalar(select(func.count()).select_from(AuditEvent))

    response = await attempt(fixture, session, version, operation)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "last_tenant_admin"
    assert await fixture.version() == version
    assert await fixture.db.scalar(select(func.count()).select_from(AuditEvent)) == audit_before
    member = await fixture.db.get(TenantMember, (fixture.tenant.id, fixture.admin.id))
    roles = tuple(
        await fixture.db.scalars(
            select(TenantRoleGrant.role).where(
                TenantRoleGrant.tenant_id == fixture.tenant.id,
                TenantRoleGrant.user_id == fixture.admin.id,
            )
        )
    )
    assert member is not None and member.status == "active"
    assert roles == ("admin",)
