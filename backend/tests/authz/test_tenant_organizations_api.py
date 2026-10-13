"""Organization directory, creation and rename use company governance."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent
from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRoleGrant,
    Tenant,
    TenantMember,
    TenantRoleGrant,
)
from tests.authz.tenant_organizations_fixture import seed_task7_case

pytest_plugins = ["tests.authz.tenant_organizations_fixture"]


async def test_admin_directory_paginates_and_rejects_cross_tenant_cursor(
    db: AsyncSession, task7_client: AsyncClient
) -> None:
    case = await seed_task7_case(task7_client, db)
    first_row = await db.get(Organization, case.org_id)
    assert first_row is not None
    first_row.updated_at = datetime.now(UTC) - timedelta(seconds=1)
    second = Organization(
        tenant_id=case.tenant_id,
        name="Second",
        slug=f"second-{uuid4().hex}",
        updated_at=datetime.now(UTC),
    )
    db.add(second)
    await db.commit()

    first = await task7_client.get(f"/api/tenants/{case.tenant_id}/organizations?limit=1")
    assert first.status_code == 200
    assert first.json()["has_more"] is True
    assert set(first.json()["items"][0]) == {
        "id",
        "name",
        "slug",
        "status",
        "is_default",
        "default_member_role",
        "member_count",
        "administrator_user_id",
        "updated_at",
    }
    cursor = first.json()["next_cursor"]
    following = await task7_client.get(
        f"/api/tenants/{case.tenant_id}/organizations", params={"limit": 1, "cursor": cursor}
    )
    assert following.status_code == 200
    assert following.json()["items"][0]["id"] != first.json()["items"][0]["id"]

    other = Tenant(name="Other", slug=f"other-{uuid4().hex}", secrets_namespace=uuid4().hex)
    db.add(other)
    await db.flush()
    db.add_all(
        (
            TenantMember(tenant_id=other.id, user_id=case.actor_id),
            TenantRoleGrant(tenant_id=other.id, user_id=case.actor_id, role="admin"),
        )
    )
    await db.commit()
    rejected = await task7_client.get(
        f"/api/tenants/{other.id}/organizations", params={"cursor": cursor}
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "invalid_cursor"


async def test_auditor_directory_redacts_management_metadata(
    db: AsyncSession, task7_client: AsyncClient
) -> None:
    case = await seed_task7_case(task7_client, db, access="auditor")
    response = await task7_client.get(f"/api/tenants/{case.tenant_id}/organizations")
    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "id": str(case.org_id),
            "name": "Existing organization",
            "member_count": 1,
        }
    ]


async def test_create_selected_admin_then_rename_with_current_etag(
    db: AsyncSession, task7_client: AsyncClient
) -> None:
    case = await seed_task7_case(task7_client, db)
    created = await task7_client.post(
        f"/api/tenants/{case.tenant_id}/organizations",
        json={
            "name": "New organization",
            "default_member_role": "builder",
            "administrator": {"user_id": str(case.target_id)},
        },
        headers=case.auth.headers(),
    )
    assert created.status_code == 201
    body = created.json()
    org_id = UUID(body["organization"]["id"])
    assert body["organization"]["slug"] == "new-organization"
    assert body["organization"]["default_member_role"] == "builder"
    assert body["administrator"] == {"kind": "member", "user_id": str(case.target_id)}
    actor_members = await db.scalar(
        select(func.count())
        .select_from(OrganizationMember)
        .where(
            OrganizationMember.org_id == org_id,
            OrganizationMember.user_id == case.actor_id,
        )
    )
    roles = tuple(
        await db.scalars(
            select(OrganizationRoleGrant.role).where(
                OrganizationRoleGrant.org_id == org_id,
                OrganizationRoleGrant.principal_id == case.target_id,
            )
        )
    )
    assert actor_members == 0 and roles == ("admin",)

    renamed = await task7_client.patch(
        f"/api/tenants/{case.tenant_id}/organizations/{org_id}",
        json={"name": "Existing organization"},
        headers={**case.auth.headers(), "If-Match": created.headers["etag"]},
    )
    assert renamed.status_code == 200
    assert renamed.json()["slug"] == "new-organization"
    stale = await task7_client.patch(
        f"/api/tenants/{case.tenant_id}/organizations/{org_id}",
        json={"name": "Stale"},
        headers={**case.auth.headers(), "If-Match": created.headers["etag"]},
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale_version"
    actions = tuple(
        await db.scalars(
            select(AuditEvent.action).where(
                AuditEvent.actor_user_id == case.actor_id,
                AuditEvent.target_id.in_((str(org_id), str(case.target_id))),
            )
        )
    )
    assert set(actions) == {"org.create", "org.member_add", "org.role_grant", "org.update"}
