"""HTTP contract for full-set company role replacement."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.models.audit_event import AuditEvent
from app.models.organization import TenantRoleGrant

if TYPE_CHECKING:
    from tests.authz.tenant_members_fixture import TenantMembersFixture

pytest_plugins = ["tests.authz.tenant_members_fixture"]


async def _role_count(fixture: TenantMembersFixture) -> int:
    return (
        await fixture.db.scalar(
            select(func.count())
            .select_from(TenantRoleGrant)
            .where(TenantRoleGrant.tenant_id == fixture.tenant.id)
        )
        or 0
    )


async def _role_audit_count(fixture: TenantMembersFixture) -> int:
    return (
        await fixture.db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action.in_(("tenant.role_grant", "tenant.role_revoke")))
        )
        or 0
    )


async def test_duplicate_roles_is_http_422_with_zero_role_audit_or_version_writes(
    tenant_members_fixture: TenantMembersFixture,
) -> None:
    fixture = tenant_members_fixture
    session = await fixture.login(fixture.admin)
    version = await fixture.version()
    roles_before = await _role_count(fixture)
    audits_before = await _role_audit_count(fixture)

    response = await fixture.client.put(
        f"/api/tenants/{fixture.tenant.id}/roles/{fixture.member.id}",
        json={"roles": ["auditor", "auditor"]},
        headers=session.headers(version),
    )

    assert response.status_code == 422
    assert await fixture.version() == version
    assert await _role_count(fixture) == roles_before
    assert await _role_audit_count(fixture) == audits_before


async def test_full_unique_role_set_accepts_admin_and_auditor_then_empty_plain_member(
    tenant_members_fixture: TenantMembersFixture,
) -> None:
    fixture = tenant_members_fixture
    session = await fixture.login(fixture.admin)
    both = await fixture.client.put(
        f"/api/tenants/{fixture.tenant.id}/roles/{fixture.member.id}",
        json={"roles": ["admin", "auditor"]},
        headers=session.headers(await fixture.version()),
    )
    assert both.status_code == 200
    assert set(both.json()["roles"]) == {"admin", "auditor"}

    cleared = await fixture.client.put(
        f"/api/tenants/{fixture.tenant.id}/roles/{fixture.member.id}",
        json={"roles": []},
        headers=session.headers(both.headers["etag"]),
    )
    assert cleared.status_code == 200
    assert cleared.json()["roles"] == []
    assert (
        await fixture.db.scalar(
            select(func.count())
            .select_from(TenantRoleGrant)
            .where(
                TenantRoleGrant.tenant_id == fixture.tenant.id,
                TenantRoleGrant.user_id == fixture.member.id,
            )
        )
        == 0
    )


async def test_suspension_retains_roles_and_inactive_member_cannot_replace_them(
    tenant_members_fixture: TenantMembersFixture,
) -> None:
    fixture = tenant_members_fixture
    session = await fixture.login(fixture.admin)
    granted = await fixture.client.put(
        f"/api/tenants/{fixture.tenant.id}/roles/{fixture.member.id}",
        json={"roles": ["auditor"]},
        headers=session.headers(await fixture.version()),
    )
    suspended = await fixture.client.patch(
        f"/api/tenants/{fixture.tenant.id}/members/{fixture.member.id}",
        json={"status": "suspended"},
        headers=session.headers(granted.headers["etag"]),
    )
    assert suspended.status_code == 200

    rejected = await fixture.client.put(
        f"/api/tenants/{fixture.tenant.id}/roles/{fixture.member.id}",
        json={"roles": []},
        headers=session.headers(suspended.headers["etag"]),
    )
    assert rejected.status_code == 404
    persisted = await fixture.db.scalar(
        select(TenantRoleGrant).where(
            TenantRoleGrant.tenant_id == fixture.tenant.id,
            TenantRoleGrant.user_id == fixture.member.id,
            TenantRoleGrant.role == "auditor",
        )
    )
    assert persisted is not None

    reactivated = await fixture.client.patch(
        f"/api/tenants/{fixture.tenant.id}/members/{fixture.member.id}",
        json={"status": "active"},
        headers=session.headers(suspended.headers["etag"]),
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["roles"] == ["auditor"]


async def test_role_list_is_paginated_and_includes_retained_suspended_grants(
    tenant_members_fixture: TenantMembersFixture,
) -> None:
    fixture = tenant_members_fixture
    session = await fixture.login(fixture.admin)
    grant = await fixture.client.put(
        f"/api/tenants/{fixture.tenant.id}/roles/{fixture.member.id}",
        json={"roles": ["auditor"]},
        headers=session.headers(await fixture.version()),
    )
    await fixture.client.patch(
        f"/api/tenants/{fixture.tenant.id}/members/{fixture.member.id}",
        json={"status": "suspended"},
        headers=session.headers(grant.headers["etag"]),
    )

    listed = await fixture.client.get(f"/api/tenants/{fixture.tenant.id}/roles?limit=2")
    assert listed.status_code == 200
    assert listed.json()["has_more"] is True
    assert len(listed.json()["items"]) == 2


async def test_platform_operator_without_membership_cannot_mutate_roles(
    tenant_members_fixture: TenantMembersFixture,
) -> None:
    fixture = tenant_members_fixture
    fixture.target.is_super_user = True
    await fixture.db.commit()
    session = await fixture.login(fixture.target)

    response = await fixture.client.put(
        f"/api/tenants/{fixture.tenant.id}/roles/{fixture.member.id}",
        json={"roles": ["auditor"]},
        headers=session.headers(await fixture.version()),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_audit_failure_rolls_back_role_and_tenant_version(
    tenant_members_fixture: TenantMembersFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.tenants import roles as role_service

    fixture = tenant_members_fixture
    session = await fixture.login(fixture.admin)
    version = await fixture.version()
    role_count = await _role_count(fixture)

    async def fail_audit(*_args: object, **_kwargs: object) -> None:
        raise SQLAlchemyError("injected role audit failure")

    monkeypatch.setattr(role_service.audit_service, "record_event", fail_audit)
    with pytest.raises(SQLAlchemyError, match="injected role audit failure"):
        await fixture.client.put(
            f"/api/tenants/{fixture.tenant.id}/roles/{fixture.member.id}",
            json={"roles": ["auditor"]},
            headers=session.headers(version),
        )

    assert await fixture.version() == version
    assert await _role_count(fixture) == role_count
