"""Negative and no-op organization directory contracts."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent
from app.models.organization import Organization, TenantMember, TenantRoleGrant
from app.services.orgs.versioning import etag
from tests.authz.tenant_organizations_fixture import seed_task7_case

pytest_plugins = ["tests.authz.tenant_organizations_fixture"]


async def test_plain_member_foreign_and_inactive_membership_are_denied(
    db: AsyncSession, task7_client: AsyncClient
) -> None:
    case = await seed_task7_case(task7_client, db)
    await db.execute(
        delete(TenantRoleGrant).where(
            TenantRoleGrant.tenant_id == case.tenant_id,
            TenantRoleGrant.user_id == case.actor_id,
        )
    )
    await db.commit()
    plain = await task7_client.get(f"/api/tenants/{case.tenant_id}/organizations")
    assert plain.status_code == 403
    assert plain.json()["error"]["code"] == "permission_required"

    foreign = await task7_client.get(f"/api/tenants/{uuid4()}/organizations")
    assert foreign.status_code == 404
    assert foreign.json()["error"]["code"] == "not_found"

    membership = await db.get(TenantMember, (case.tenant_id, case.actor_id))
    assert membership is not None
    membership.status = "suspended"
    await db.commit()
    inactive = await task7_client.get(f"/api/tenants/{case.tenant_id}/organizations")
    assert inactive.status_code == 404
    assert inactive.json()["error"]["code"] == "not_found"


async def test_archived_rename_is_denied_without_version_or_audit_change(
    db: AsyncSession, task7_client: AsyncClient
) -> None:
    case = await seed_task7_case(task7_client, db)
    organization = await db.get(Organization, case.org_id)
    assert organization is not None
    organization.status = "archived"
    organization.archived_at = datetime.now(UTC)
    version = organization.updated_at
    await db.commit()
    audits = await _update_audits(db, case.actor_id, case.org_id)
    response = await task7_client.patch(
        f"/api/tenants/{case.tenant_id}/organizations/{case.org_id}",
        json={"name": "Rejected rename"},
        headers={**case.auth.headers(), "If-Match": etag(version)},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "organization_unavailable"
    await db.refresh(organization)
    assert organization.name == "Existing organization"
    assert organization.updated_at == version
    assert await _update_audits(db, case.actor_id, case.org_id) == audits


async def test_noop_rename_preserves_etag_version_and_audit_count(
    db: AsyncSession, task7_client: AsyncClient
) -> None:
    case = await seed_task7_case(task7_client, db)
    organization = await db.get(Organization, case.org_id)
    assert organization is not None
    before = etag(organization.updated_at)
    audits = await _update_audits(db, case.actor_id, case.org_id)
    response = await task7_client.patch(
        f"/api/tenants/{case.tenant_id}/organizations/{case.org_id}",
        json={"name": organization.name},
        headers={**case.auth.headers(), "If-Match": before},
    )
    assert response.status_code == 200
    assert response.headers["etag"] == before
    await db.refresh(organization)
    assert etag(organization.updated_at) == before
    assert await _update_audits(db, case.actor_id, case.org_id) == audits


async def _update_audits(db: AsyncSession, actor_id: UUID, org_id: UUID) -> int:
    count = await db.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(
            AuditEvent.actor_user_id == actor_id,
            AuditEvent.target_id == str(org_id),
            AuditEvent.action == "org.update",
        )
    )
    assert count is not None
    return count
