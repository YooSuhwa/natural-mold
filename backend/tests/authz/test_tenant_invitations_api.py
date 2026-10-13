"""Company invitation administration through the actual ASGI application."""

from __future__ import annotations

from typing import TYPE_CHECKING

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


async def _etag(db: AsyncSession, client: AsyncClient, fixture: OrgFixture) -> str:
    fixture.session.apply(client)
    tenant = await db.get(Tenant, fixture.tenant)
    assert tenant is not None
    return etag(tenant.updated_at)


async def _grant_admin(db: AsyncSession, fixture: OrgFixture) -> None:
    db.add(
        TenantRoleGrant(
            tenant_id=fixture.tenant,
            user_id=fixture.user.id,
            role="admin",
            granted_by=fixture.user.id,
        )
    )
    await db.commit()


async def test_create_retry_list_revoke_and_repeat_resend_are_hash_only(
    db: AsyncSession,
    tenant_invitation_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    await _grant_admin(db, org_fixture)
    client = tenant_invitation_client
    current_etag = await _etag(db, client, org_fixture)
    path = f"/api/tenants/{org_fixture.tenant}/invitations"
    headers = {**org_fixture.session.headers(), "If-Match": current_etag}

    created = await client.post(
        path,
        json={"email": "company-new@example.com", "roles": ["auditor"]},
        headers=headers,
    )
    assert created.status_code == 201
    assert created.json()["token"].startswith("tv1.")
    invitation_id, token = created.json()["id"], created.json()["token"]
    retried = await client.post(
        path,
        json={"email": "company-new@example.com", "roles": ["auditor"]},
        headers={**org_fixture.session.headers(), "If-Match": current_etag},
    )
    assert retried.status_code == 200
    assert (retried.json()["id"], retried.json()["token"]) == (invitation_id, token)

    listed = await client.get(path)
    assert listed.status_code == 200
    assert listed.json()["items"] == [
        {key: value for key, value in created.json().items() if key != "token"}
    ]
    resent = await client.post(
        f"{path}/{invitation_id}/resend",
        headers={**org_fixture.session.headers(), "If-Match": retried.headers["etag"]},
    )
    assert resent.status_code == 201
    repeated = await client.post(
        f"{path}/{invitation_id}/resend",
        headers={**org_fixture.session.headers(), "If-Match": resent.headers["etag"]},
    )
    assert repeated.status_code == 200
    assert repeated.json() == resent.json()
    revoked = await client.delete(
        f"{path}/{resent.json()['id']}",
        headers={**org_fixture.session.headers(), "If-Match": repeated.headers["etag"]},
    )
    assert revoked.status_code == 204
    assert "token_hash" not in listed.text
    assert token not in listed.text
    actions = await db.execute(
        select(AuditEvent.action, func.count())
        .where(AuditEvent.action.like("tenant.invitation_%"))
        .group_by(AuditEvent.action)
    )
    assert sorted((row[0], row[1]) for row in actions.all()) == [
        ("tenant.invitation_create", 1),
        ("tenant.invitation_resend", 1),
        ("tenant.invitation_revoke", 1),
    ]
    rows = list((await db.scalars(select(TenantInvitation))).all())
    assert len(rows) == 2 and all(row.token_hash != token for row in rows)


async def test_mutations_require_csrf_current_etag_and_single_role(
    db: AsyncSession,
    tenant_invitation_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    await _grant_admin(db, org_fixture)
    client = tenant_invitation_client
    current_etag = await _etag(db, client, org_fixture)
    path = f"/api/tenants/{org_fixture.tenant}/invitations"
    no_csrf = await client.post(
        path,
        json={"email": "csrf-company@example.com", "roles": []},
        headers={"If-Match": current_etag},
    )
    assert no_csrf.status_code == 403
    invalid_roles = await client.post(
        path,
        json={"email": "roles-company@example.com", "roles": ["admin", "auditor"]},
        headers={**org_fixture.session.headers(), "If-Match": current_etag},
    )
    assert invalid_roles.status_code == 422
    stale = await client.post(
        path,
        json={"email": "stale-company@example.com", "roles": []},
        headers={**org_fixture.session.headers(), "If-Match": '"stale"'},
    )
    assert stale.status_code == 409
    assert await db.scalar(select(func.count()).select_from(TenantInvitation)) == 0
