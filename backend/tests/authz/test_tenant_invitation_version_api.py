"""Company invitation create enforces the tenant version header syntax."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent
from app.models.organization import Tenant, TenantRoleGrant
from app.models.tenant_invitation import TenantInvitation
from app.services.tenants.versioning import etag

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = [
    "tests.authz.org_context_fixture",
    "tests.authz.tenant_invitation_fixture",
]


async def _grant_admin(db: AsyncSession, fixture: OrgFixture) -> str:
    db.add(
        TenantRoleGrant(
            tenant_id=fixture.tenant,
            user_id=fixture.user.id,
            role="admin",
            granted_by=fixture.user.id,
        )
    )
    await db.commit()
    tenant = await db.get(Tenant, fixture.tenant)
    assert tenant is not None
    return etag(tenant.updated_at)


@pytest.mark.parametrize("invalid_header", [None, "malformed", "unquoted", '"unbalanced'])
async def test_identical_retry_rejects_missing_or_malformed_version_without_effects(
    db: AsyncSession,
    tenant_invitation_client: AsyncClient,
    org_fixture: OrgFixture,
    invalid_header: str | None,
) -> None:
    client = tenant_invitation_client
    org_fixture.session.apply(client)
    initial_etag = await _grant_admin(db, org_fixture)
    path = f"/api/tenants/{org_fixture.tenant}/invitations"
    payload = {"email": "version-retry@example.com", "roles": ["auditor"]}
    created = await client.post(
        path,
        json=payload,
        headers={**org_fixture.session.headers(), "If-Match": initial_etag},
    )
    assert created.status_code == 201
    row = await db.scalar(select(TenantInvitation))
    tenant = await db.get(Tenant, org_fixture.tenant)
    assert row is not None and tenant is not None
    before = (row.id, row.token_hash, row.status, tenant.updated_at)

    headers = org_fixture.session.headers()
    if invalid_header is not None:
        headers["If-Match"] = invalid_header
    rejected = await client.post(path, json=payload, headers=headers)

    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "stale_version"
    db.expire_all()
    persisted = await db.scalar(select(TenantInvitation))
    refreshed_tenant = await db.get(Tenant, org_fixture.tenant)
    assert persisted is not None and refreshed_tenant is not None
    assert (
        persisted.id,
        persisted.token_hash,
        persisted.status,
        refreshed_tenant.updated_at,
    ) == before
    assert await db.scalar(select(func.count()).select_from(TenantInvitation)) == 1
    assert (
        await db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "tenant.invitation_create")
        )
        == 1
    )


async def test_valid_stale_identical_retry_is_idempotent_but_new_content_is_rejected(
    db: AsyncSession,
    tenant_invitation_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    client = tenant_invitation_client
    org_fixture.session.apply(client)
    stale_etag = await _grant_admin(db, org_fixture)
    path = f"/api/tenants/{org_fixture.tenant}/invitations"
    headers = {**org_fixture.session.headers(), "If-Match": stale_etag}
    payload = {"email": "quoted-stale@example.com", "roles": []}
    created = await client.post(path, json=payload, headers=headers)
    assert created.status_code == 201

    retried = await client.post(path, json=payload, headers=headers)
    rejected = await client.post(
        path,
        json={"email": "different-content@example.com", "roles": []},
        headers=headers,
    )

    assert retried.status_code == 200
    assert retried.json() == created.json()
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "stale_version"
    assert await db.scalar(select(func.count()).select_from(TenantInvitation)) == 1
    assert (
        await db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "tenant.invitation_create")
        )
        == 1
    )
