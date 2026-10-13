"""Idempotent HTTP writes keep company versions and audits stable."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from app.models.audit_event import AuditEvent

if TYPE_CHECKING:
    from tests.authz.tenant_members_fixture import TenantMembersFixture

pytest_plugins = ["tests.authz.tenant_members_fixture"]


async def audit_count(fixture: TenantMembersFixture, action: str, target_id: str) -> int:
    return (
        await fixture.db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == action, AuditEvent.target_id == target_id)
        )
        or 0
    )


async def test_repeated_member_add_keeps_version_and_single_audit(
    tenant_members_fixture: TenantMembersFixture,
) -> None:
    fixture = tenant_members_fixture
    session = await fixture.login(fixture.admin)
    first = await fixture.client.post(
        f"/api/tenants/{fixture.tenant.id}/members",
        json={"user_id": str(fixture.target.id)},
        headers=session.headers(await fixture.version()),
    )
    assert first.status_code == 201
    version = first.headers["etag"]

    repeated = await fixture.client.post(
        f"/api/tenants/{fixture.tenant.id}/members",
        json={"user_id": str(fixture.target.id)},
        headers=session.headers(version),
    )
    assert repeated.status_code == 201
    assert repeated.headers["etag"] == version
    assert await audit_count(fixture, "tenant.member_add", str(fixture.target.id)) == 1


async def test_repeated_status_sets_emit_only_transition_audits(
    tenant_members_fixture: TenantMembersFixture,
) -> None:
    fixture = tenant_members_fixture
    session = await fixture.login(fixture.admin)
    initial = await fixture.version()
    no_op = await fixture.client.patch(
        f"/api/tenants/{fixture.tenant.id}/members/{fixture.member.id}",
        json={"status": "active"},
        headers=session.headers(initial),
    )
    assert no_op.status_code == 200
    assert no_op.headers["etag"] == initial

    suspended = await fixture.client.patch(
        f"/api/tenants/{fixture.tenant.id}/members/{fixture.member.id}",
        json={"status": "suspended"},
        headers=session.headers(initial),
    )
    assert suspended.status_code == 200
    version = suspended.headers["etag"]
    repeated = await fixture.client.patch(
        f"/api/tenants/{fixture.tenant.id}/members/{fixture.member.id}",
        json={"status": "suspended"},
        headers=session.headers(version),
    )
    assert repeated.status_code == 200
    assert repeated.headers["etag"] == version
    assert await audit_count(fixture, "tenant.member_suspend", str(fixture.member.id)) == 1

    active = await fixture.client.patch(
        f"/api/tenants/{fixture.tenant.id}/members/{fixture.member.id}",
        json={"status": "active"},
        headers=session.headers(version),
    )
    assert active.status_code == 200
    active_version = active.headers["etag"]
    active_again = await fixture.client.patch(
        f"/api/tenants/{fixture.tenant.id}/members/{fixture.member.id}",
        json={"status": "active"},
        headers=session.headers(active_version),
    )
    assert active_again.status_code == 200
    assert active_again.headers["etag"] == active_version
    assert await audit_count(fixture, "tenant.member_add", str(fixture.member.id)) == 1


async def test_repeated_full_role_set_keeps_version_and_audit_counts(
    tenant_members_fixture: TenantMembersFixture,
) -> None:
    fixture = tenant_members_fixture
    session = await fixture.login(fixture.admin)
    first = await fixture.client.put(
        f"/api/tenants/{fixture.tenant.id}/roles/{fixture.member.id}",
        json={"roles": ["admin", "auditor"]},
        headers=session.headers(await fixture.version()),
    )
    assert first.status_code == 200
    version = first.headers["etag"]
    repeated = await fixture.client.put(
        f"/api/tenants/{fixture.tenant.id}/roles/{fixture.member.id}",
        json={"roles": ["auditor", "admin"]},
        headers=session.headers(version),
    )
    assert repeated.status_code == 200
    assert repeated.headers["etag"] == version
    assert await audit_count(fixture, "tenant.role_grant", str(fixture.member.id)) == 1
    assert await audit_count(fixture, "tenant.role_revoke", str(fixture.member.id)) == 0
