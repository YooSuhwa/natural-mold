"""HTTP contract for company member administration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from app.models.audit_event import AuditEvent
from app.models.organization import TenantMember, TenantRoleGrant

if TYPE_CHECKING:
    from tests.authz.tenant_members_fixture import TenantMembersFixture

pytest_plugins = ["tests.authz.tenant_members_fixture"]


async def test_admin_lists_members_with_cursor_and_tenant_etag(
    tenant_members_fixture: TenantMembersFixture,
) -> None:
    fixture = tenant_members_fixture
    await fixture.login(fixture.admin)

    first = await fixture.client.get(f"/api/tenants/{fixture.tenant.id}/members?limit=2")
    assert first.status_code == 200
    assert first.headers["etag"] == await fixture.version()
    assert len(first.json()["items"]) == 2
    assert first.json()["has_more"] is True
    assert all("email" in item for item in first.json()["items"])

    second = await fixture.client.get(
        f"/api/tenants/{fixture.tenant.id}/members",
        params={"limit": 2, "cursor": first.json()["next_cursor"]},
    )
    assert second.status_code == 200
    assert len(second.json()["items"]) == 1
    assert second.json()["has_more"] is False


async def test_post_adds_only_known_active_account_and_audits_once(
    tenant_members_fixture: TenantMembersFixture,
) -> None:
    fixture = tenant_members_fixture
    session = await fixture.login(fixture.admin)
    before = await fixture.version()

    response = await fixture.client.post(
        f"/api/tenants/{fixture.tenant.id}/members",
        json={"user_id": str(fixture.target.id)},
        headers=session.headers(before),
    )

    assert response.status_code == 201
    assert response.headers["etag"] != before
    member = await fixture.db.get(TenantMember, (fixture.tenant.id, fixture.target.id))
    assert member is not None and member.status == "active"
    assert (
        await fixture.db.scalar(
            select(func.count())
            .select_from(TenantRoleGrant)
            .where(
                TenantRoleGrant.tenant_id == fixture.tenant.id,
                TenantRoleGrant.user_id == fixture.target.id,
            )
        )
        == 0
    )
    assert (
        await fixture.db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.action == "tenant.member_add",
                AuditEvent.target_id == str(fixture.target.id),
            )
        )
        == 1
    )


async def test_post_rejects_inactive_global_account_without_writes(
    tenant_members_fixture: TenantMembersFixture,
) -> None:
    fixture = tenant_members_fixture
    session = await fixture.login(fixture.admin)
    before = await fixture.version()

    response = await fixture.client.post(
        f"/api/tenants/{fixture.tenant.id}/members",
        json={"user_id": str(fixture.inactive.id)},
        headers=session.headers(before),
    )

    assert response.status_code == 404
    assert await fixture.version() == before
    assert await fixture.db.get(TenantMember, (fixture.tenant.id, fixture.inactive.id)) is None


async def test_stale_version_precedes_last_admin_then_fresh_retry_is_last_admin(
    tenant_members_fixture: TenantMembersFixture,
) -> None:
    fixture = tenant_members_fixture
    session = await fixture.login(fixture.admin)
    initial = await fixture.version()
    peer = await fixture.client.patch(
        f"/api/tenants/{fixture.tenant.id}/members/{fixture.peer_admin.id}",
        json={"status": "suspended"},
        headers=session.headers(initial),
    )
    assert peer.status_code == 200
    current = peer.headers["etag"]

    stale = await fixture.client.patch(
        f"/api/tenants/{fixture.tenant.id}/members/{fixture.admin.id}",
        json={"status": "suspended"},
        headers=session.headers(initial),
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_version"

    invariant = await fixture.client.patch(
        f"/api/tenants/{fixture.tenant.id}/members/{fixture.admin.id}",
        json={"status": "suspended"},
        headers=session.headers(current),
    )
    assert invariant.status_code == 409
    assert invariant.json()["error"]["code"] == "last_tenant_admin"


async def test_delete_consumes_company_exit_and_bumps_tenant_version(
    tenant_members_fixture: TenantMembersFixture,
) -> None:
    fixture = tenant_members_fixture
    session = await fixture.login(fixture.admin)
    before = await fixture.version()

    response = await fixture.client.delete(
        f"/api/tenants/{fixture.tenant.id}/members/{fixture.member.id}",
        headers=session.headers(before),
    )

    assert response.status_code == 204
    assert response.headers["etag"] != before
    member = await fixture.db.get(TenantMember, (fixture.tenant.id, fixture.member.id))
    assert member is not None and member.status == "removed"
    assert (
        await fixture.db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.action == "tenant.member_remove",
                AuditEvent.target_id == str(fixture.member.id),
            )
        )
        == 1
    )


async def test_plain_member_cannot_read_or_mutate_company_members(
    tenant_members_fixture: TenantMembersFixture,
) -> None:
    fixture = tenant_members_fixture
    session = await fixture.login(fixture.member)
    listed = await fixture.client.get(f"/api/tenants/{fixture.tenant.id}/members")
    changed = await fixture.client.patch(
        f"/api/tenants/{fixture.tenant.id}/members/{fixture.member.id}",
        json={"status": "suspended"},
        headers=session.headers(await fixture.version()),
    )
    assert listed.status_code == 403
    assert changed.status_code == 403
    assert listed.json()["error"]["code"] == "permission_required"
    assert changed.json()["error"]["code"] == "permission_required"
