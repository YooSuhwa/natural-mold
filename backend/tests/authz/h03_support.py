"""Seeds for the H-03 organization audit and usage tests (plan 14.1, 9.6, S26)."""

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.models.audit_event import AuditEvent
from app.models.organization import Organization, OrganizationRoleGrant, Tenant, TenantRoleGrant
from tests.authz.e01_support import FOREIGN_USER_ID, OTHER_USER_ID, seed
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID

FAR_TENANT_ID = uuid5(NAMESPACE_URL, "moldy/h03-far-tenant")
FAR_ORG_ID = uuid5(NAMESPACE_URL, "moldy/h03-far-organization")
OTHER = CurrentUser(id=OTHER_USER_ID, email="other@test.com", name="Other", is_super_user=False)
FOREIGN = CurrentUser(id=FOREIGN_USER_ID, email="foreign@test.com", name="F", is_super_user=False)
ME = CurrentUser(id=TEST_USER_ID, email="test@test.com", name="Test User", is_super_user=True)

__all__ = ["FOREIGN_USER_ID", "OTHER_USER_ID", "TEST_ORG_ID", "TEST_TENANT_ID", "TEST_USER_ID"]


async def seed_world(db: AsyncSession) -> None:
    """Unit org (me + OTHER), sibling org (FOREIGN) and an unrelated company with one org."""
    await seed(db)
    if await db.get(Tenant, FAR_TENANT_ID) is None:
        db.add(
            Tenant(
                id=FAR_TENANT_ID,
                name="Far company",
                slug="h03-far",
                secrets_namespace=f"tenants/{FAR_TENANT_ID}",
            )
        )
        await db.flush()
        db.add(Organization(id=FAR_ORG_ID, tenant_id=FAR_TENANT_ID, name="Far", slug="h03-far"))
    await db.commit()


async def grant_org_role(db: AsyncSession, user_id: UUID, role: str) -> None:
    db.add(
        OrganizationRoleGrant(
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            role=role,
            principal_type="user",
            principal_id=user_id,
        )
    )
    await db.commit()


async def grant_company_role(db: AsyncSession, user_id: UUID, role: str) -> None:
    db.add(TenantRoleGrant(tenant_id=TEST_TENANT_ID, user_id=user_id, role=role))
    await db.commit()


async def add_event(
    db: AsyncSession,
    action: str,
    *,
    tenant: UUID | None = TEST_TENANT_ID,
    org: UUID | None = TEST_ORG_ID,
    minutes_ago: int = 0,
    actor: UUID | None = OTHER_USER_ID,
) -> AuditEvent:
    row = AuditEvent(
        id=uuid4(),
        tenant_id=tenant,
        org_id=org,
        actor_type="user",
        actor_user_id=actor,
        action=action,
        target_type="agent",
        outcome="success",
        created_at=(datetime.now(UTC) - timedelta(minutes=minutes_ago)).replace(tzinfo=None),
        ip_address="203.0.113.7",
        user_agent="agent/1.0",
    )
    db.add(row)
    await db.commit()
    return row
