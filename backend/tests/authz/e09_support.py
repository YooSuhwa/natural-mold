"""Seeds for E-09 company publication (plan 9.7, 10.2, S05)."""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.dependencies import CurrentUser, get_current_user, get_org_context
from app.models.audit_event import AuditEvent
from app.models.organization import OrganizationMember, Tenant, TenantMember, TenantRoleGrant
from app.models.user import User
from tests.authz.d04_support import OTHER_ORG_ID
from tests.authz.unit_scope_fixture import TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession

OUTSIDER_ID = uuid5(NAMESPACE_URL, "moldy/e09-outsider")
TENANT = f"/api/tenants/{TEST_TENANT_ID}"
MEMBER = CurrentUser(id=TEST_USER_ID, email="test@test.com", name="T", is_super_user=False)
OUTSIDER = CurrentUser(id=OUTSIDER_ID, email="out@test.com", name="Out", is_super_user=False)


async def seed_outsider(db: AsyncSession) -> None:
    """A member of the sibling organization of the same company."""
    if await db.get(User, OUTSIDER_ID) is None:
        db.add(User(id=OUTSIDER_ID, email=OUTSIDER.email, name=OUTSIDER.name, is_active=True))
        await db.flush()
        db.add(TenantMember(tenant_id=TEST_TENANT_ID, user_id=OUTSIDER_ID))
        db.add(
            OrganizationMember(org_id=OTHER_ORG_ID, tenant_id=TEST_TENANT_ID, user_id=OUTSIDER_ID)
        )
    await db.commit()


async def make_admin(db: AsyncSession, user_id: UUID = TEST_USER_ID) -> None:
    db.add(TenantRoleGrant(tenant_id=TEST_TENANT_ID, user_id=user_id, role="admin"))
    await db.commit()


async def set_policy(db: AsyncSession, policy: str) -> None:
    tenant = await db.get(Tenant, TEST_TENANT_ID)
    assert tenant is not None
    tenant.settings = {**(tenant.settings or {}), "marketplace_tenant_publish": policy}
    await db.commit()


async def actions(target_id: UUID | str) -> list[AuditEvent]:
    async with TestSession() as db:
        rows = await db.scalars(
            select(AuditEvent)
            .where(AuditEvent.target_id == str(target_id))
            .order_by(AuditEvent.created_at)
        )
        return list(rows)


@contextmanager
def acting_as(app: FastAPI, user: CurrentUser, org_id: UUID) -> Iterator[None]:
    """Swap the session user and its current organization for the block."""
    saved = {dep: app.dependency_overrides.get(dep) for dep in (get_current_user, get_org_context)}

    async def current() -> CurrentUser:
        return user

    async def context() -> OrgContext:
        return OrgContext(tenant_id=TEST_TENANT_ID, org_id=org_id, user_id=user.id)

    app.dependency_overrides[get_current_user] = current
    app.dependency_overrides[get_org_context] = context
    try:
        yield
    finally:
        for dep, previous in saved.items():
            if previous is not None:
                app.dependency_overrides[dep] = previous
