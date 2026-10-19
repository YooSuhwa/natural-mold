"""Company admins reuse B04 paths without receiving organization membership."""

from collections import Counter
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent
from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRoleGrant,
    TenantMember,
)
from app.models.user import User
from tests.authz.tenant_organizations_fixture import seed_task7_case

pytest_plugins = ["tests.authz.tenant_organizations_fixture"]


async def test_company_admin_adds_replacement_before_removing_old_admin(
    db: AsyncSession, task7_client: AsyncClient
) -> None:
    case = await seed_task7_case(task7_client, db)
    replacement = User(email=f"replacement-{uuid4().hex}@example.com", name="Replacement")
    unrelated = User(email=f"unrelated-{uuid4().hex}@example.com", name="Unrelated")
    db.add_all((replacement, unrelated))
    await db.flush()
    db.add_all(
        (
            TenantMember(tenant_id=case.tenant_id, user_id=replacement.id),
            TenantMember(tenant_id=case.tenant_id, user_id=unrelated.id),
            OrganizationMember(org_id=case.org_id, tenant_id=case.tenant_id, user_id=unrelated.id),
            OrganizationRoleGrant(
                org_id=case.org_id,
                tenant_id=case.tenant_id,
                principal_type="user",
                principal_id=unrelated.id,
                role="auditor",
                granted_by=case.actor_id,
            ),
        )
    )
    await db.commit()
    actions_before = Counter(
        await db.scalars(
            select(AuditEvent.action).where(
                AuditEvent.actor_user_id == case.actor_id,
                AuditEvent.action.in_(("org.member_add", "org.role_grant", "org.role_revoke")),
            )
        )
    )

    roster = await task7_client.get(f"/api/orgs/{case.org_id}/members")
    added = await task7_client.post(
        f"/api/orgs/{case.org_id}/members",
        json={"user_id": str(replacement.id)},
        headers={**case.auth.headers(), "If-Match": roster.headers["etag"]},
    )
    assert added.status_code == 201
    assert added.headers["etag"] != roster.headers["etag"]
    roles = await task7_client.get(f"/api/orgs/{case.org_id}/roles")
    promoted = await task7_client.put(
        f"/api/orgs/{case.org_id}/roles",
        json={"principal": {"type": "user", "id": str(replacement.id)}, "roles": ["admin"]},
        headers={**case.auth.headers(), "If-Match": roles.headers["etag"]},
    )
    assert promoted.status_code == 200
    assert promoted.headers["etag"] != added.headers["etag"]
    demoted = await task7_client.put(
        f"/api/orgs/{case.org_id}/roles",
        json={"principal": {"type": "user", "id": str(case.target_id)}, "roles": []},
        headers={**case.auth.headers(), "If-Match": promoted.headers["etag"]},
    )
    assert demoted.status_code == 200
    assert demoted.headers["etag"] != promoted.headers["etag"]
    current = tuple(
        await db.scalars(
            select(OrganizationRoleGrant.principal_id).where(
                OrganizationRoleGrant.org_id == case.org_id,
                OrganizationRoleGrant.role == "admin",
            )
        )
    )
    actor_membership = await db.get(OrganizationMember, (case.org_id, case.actor_id))
    unrelated_roles = tuple(
        await db.scalars(
            select(OrganizationRoleGrant.role).where(
                OrganizationRoleGrant.org_id == case.org_id,
                OrganizationRoleGrant.principal_id == unrelated.id,
            )
        )
    )
    organization = await db.get(Organization, case.org_id)
    assert organization is not None
    assert current == (replacement.id,)
    assert actor_membership is None
    assert unrelated_roles == ("auditor",)
    assert organization.settings == {"default_member_role": "member", "preserved": True}
    actions_after = Counter(
        await db.scalars(
            select(AuditEvent.action).where(
                AuditEvent.actor_user_id == case.actor_id,
                AuditEvent.action.in_(("org.member_add", "org.role_grant", "org.role_revoke")),
            )
        )
    )
    assert actions_after - actions_before == Counter(
        {"org.member_add": 1, "org.role_grant": 1, "org.role_revoke": 1}
    )


async def test_company_admin_changes_default_role_without_org_membership(
    db: AsyncSession, task7_client: AsyncClient
) -> None:
    case = await seed_task7_case(task7_client, db)
    original = await task7_client.get(f"/api/orgs/{case.org_id}")
    changed = await task7_client.patch(
        f"/api/orgs/{case.org_id}",
        json={"settings": {"default_member_role": "builder"}},
        headers={**case.auth.headers(), "If-Match": original.headers["etag"]},
    )
    assert changed.status_code == 200
    assert changed.json()["settings"]["default_member_role"] == "builder"
    assert await db.get(OrganizationMember, (case.org_id, case.actor_id)) is None
