"""Actual B04 HTTP writes acquire the company parent before the organization."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState, Session
from sqlalchemy.sql import Select

from app.models.organization import OrganizationMember, OrganizationRoleGrant, TenantMember
from app.models.user import User

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


async def grant_admin(db: AsyncSession, fixture: OrgFixture) -> None:
    db.add(
        OrganizationRoleGrant(
            org_id=fixture.first,
            tenant_id=fixture.tenant,
            principal_type="user",
            principal_id=fixture.user.id,
            role="admin",
            granted_by=fixture.user.id,
        )
    )
    await db.commit()


@contextmanager
def parent_lock_trace() -> Iterator[list[tuple[str, ...]]]:
    locked_tables: list[tuple[str, ...]] = []

    def capture(state: ORMExecuteState) -> None:
        statement = state.statement
        if not isinstance(statement, Select):
            return
        if getattr(statement, "_for_update_arg", None) is None:
            return
        names = tuple(
            name
            for table in statement.get_final_froms()
            if (name := str(table)) in {"tenants", "organizations"}
        )
        if names:
            locked_tables.append(names)

    event.listen(Session, "do_orm_execute", capture)
    try:
        yield locked_tables
    finally:
        event.remove(Session, "do_orm_execute", capture)


def assert_tenant_then_organization(locked_tables: list[tuple[str, ...]]) -> None:
    parents = [name for tables in locked_tables for name in tables]
    assert parents[:2] == ["tenants", "organizations"], locked_tables


async def test_member_post_locks_tenant_before_organization_over_real_http(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    # Given: an authenticated organization admin and an active company-member target.
    await grant_admin(db, org_fixture)
    target = User(email="b04-lock-target@example.test", name="B04 lock target")
    db.add(target)
    await db.flush()
    db.add(TenantMember(tenant_id=org_fixture.tenant, user_id=target.id))
    await db.commit()
    listed = await raw_client.get(f"/api/orgs/{org_fixture.first}/members")
    assert listed.status_code == 200
    # When: the real ASGI route performs a CSRF- and If-Match-protected write.
    with parent_lock_trace() as locked_tables:
        response = await raw_client.post(
            f"/api/orgs/{org_fixture.first}/members",
            json={"user_id": str(target.id)},
            headers={**org_fixture.session.headers(), "If-Match": listed.headers["etag"]},
        )

    # Then: the successful write's first parent lock is Tenant, never Organization.
    assert response.status_code == 201
    assert_tenant_then_organization(locked_tables)


async def test_group_post_locks_tenant_before_organization_over_real_http(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    # Given: an authenticated admin and the current organization version.
    await grant_admin(db, org_fixture)
    listed = await raw_client.get(f"/api/orgs/{org_fixture.first}/groups")
    assert listed.status_code == 200

    # When: the real group route creates a manual group.
    with parent_lock_trace() as locked_tables:
        response = await raw_client.post(
            f"/api/orgs/{org_fixture.first}/groups",
            json={"name": "B04 traced group"},
            headers={**org_fixture.session.headers(), "If-Match": listed.headers["etag"]},
        )

    # Then: parent locks are Tenant then Organization.
    assert response.status_code == 201
    assert_tenant_then_organization(locked_tables)


async def test_role_put_locks_tenant_before_organization_over_real_http(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    # Given: an authenticated admin and an active organization-member target.
    await grant_admin(db, org_fixture)
    target = User(email="b04-role-target@example.test", name="B04 role target")
    db.add(target)
    await db.flush()
    db.add_all(
        (
            TenantMember(tenant_id=org_fixture.tenant, user_id=target.id),
            OrganizationMember(
                org_id=org_fixture.first,
                tenant_id=org_fixture.tenant,
                user_id=target.id,
            ),
        )
    )
    await db.commit()
    listed = await raw_client.get(f"/api/orgs/{org_fixture.first}/roles")
    assert listed.status_code == 200

    # When: the real role route grants one role.
    with parent_lock_trace() as locked_tables:
        response = await raw_client.put(
            f"/api/orgs/{org_fixture.first}/roles",
            json={"principal": {"type": "user", "id": str(target.id)}, "roles": ["builder"]},
            headers={**org_fixture.session.headers(), "If-Match": listed.headers["etag"]},
        )

    # Then: parent locks are Tenant then Organization.
    assert response.status_code == 200
    assert_tenant_then_organization(locked_tables)
