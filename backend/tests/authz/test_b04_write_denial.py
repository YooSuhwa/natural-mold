"""B04 denials stay before every implicit database write boundary."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Literal
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Request
from httpx import AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_control import OrgControlContext
from app.dependencies import CurrentUser, get_current_user, get_db
from app.exceptions import AppError
from app.models.audit_event import AuditEvent
from app.models.organization import Organization, OrganizationRoleGrant, Tenant, TenantMember
from app.models.user import User
from app.schemas.org_members import MemberCreate
from app.services.orgs.info import OrgMutationRequest
from app.services.orgs.members import create_member
from app.services.orgs.versioning import etag

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]

Denial = Literal["stale", "missing", "inactive", "foreign", "unauthorized"]


def mutation_request(db: AsyncSession, expected_version: str | None) -> OrgMutationRequest:
    request = Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/orgs/test/members",
            "raw_path": b"/api/orgs/test/members",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1),
            "server": ("test", 80),
        }
    )
    return OrgMutationRequest(db=db, request=request, expected_version=expected_version)


async def denial_target(
    db: AsyncSession, fixture: OrgFixture, scenario: Denial
) -> tuple[UUID, str]:
    if scenario == "inactive":
        user = User(email="b04-inactive@example.test", name="Inactive", is_active=False)
        db.add(user)
        await db.flush()
        db.add(TenantMember(tenant_id=fixture.tenant, user_id=user.id))
        return user.id, "not_found"
    if scenario == "foreign":
        tenant = Tenant(name="Foreign target", slug="b04-foreign", secrets_namespace="b04/foreign")
        user = User(email="b04-foreign@example.test", name="Foreign")
        db.add_all((tenant, user))
        await db.flush()
        db.add(TenantMember(tenant_id=tenant.id, user_id=user.id))
        return user.id, "not_found"
    return uuid4(), "permission_required" if scenario == "unauthorized" else (
        "stale_version" if scenario == "stale" else "not_found"
    )


@pytest.mark.parametrize("scenario", ["stale", "missing", "inactive", "foreign", "unauthorized"])
async def test_member_denial_does_not_autoflush_pending_rows(
    db: AsyncSession,
    org_fixture: OrgFixture,
    scenario: Denial,
) -> None:
    # Given: a stale route context plus one denial case and an unrelated pending Tenant.
    organization = await db.get(Organization, org_fixture.first)
    assert organization is not None
    if scenario != "unauthorized":
        db.add(
            OrganizationRoleGrant(
                org_id=organization.id,
                tenant_id=organization.tenant_id,
                principal_type="user",
                principal_id=org_fixture.user.id,
                role="admin",
                granted_by=org_fixture.user.id,
            )
        )
    target_id, expected_code = await denial_target(db, org_fixture, scenario)
    await db.commit()
    audit_before = await db.scalar(select(func.count()).select_from(AuditEvent))
    pending = Tenant(
        name=f"Pending {scenario}",
        slug=f"b04-pending-{scenario}-{uuid4().hex}",
        secrets_namespace=f"b04/pending/{scenario}/{uuid4().hex}",
    )
    db.add(pending)
    flushes = 0

    def count_flushes(*_args) -> None:
        nonlocal flushes
        flushes += 1

    context = OrgControlContext(
        organization=organization,
        actor=org_fixture.user,
        permissions=("can_manage_members",),
    )
    expected_version = None if scenario == "stale" else etag(organization.updated_at)

    # When: the real service revalidates authority, version and target state.
    event.listen(db.sync_session, "before_flush", count_flushes)
    try:
        with pytest.raises(AppError) as raised:
            await create_member(
                mutation_request(db, expected_version), context, MemberCreate(user_id=target_id)
            )
    finally:
        event.remove(db.sync_session, "before_flush", count_flushes)

    # Then: denial is typed and no pending row or audit crossed the write boundary.
    assert raised.value.code == expected_code
    with db.no_autoflush:
        pending_visible = await db.scalar(select(Tenant.id).where(Tenant.id == pending.id))
        audit_after = await db.scalar(select(func.count()).select_from(AuditEvent))
    assert flushes == 0
    assert pending_visible is None and pending in db.new
    assert audit_after == audit_before


@pytest.mark.parametrize("actor_source", ["real_jwt", "current_user_override"])
async def test_route_stale_denial_does_not_autoflush_pending_rows(
    db: AsyncSession,
    raw_client: AsyncClient,
    raw_test_app: FastAPI,
    org_fixture: OrgFixture,
    actor_source: Literal["real_jwt", "current_user_override"],
) -> None:
    organization = await db.get(Organization, org_fixture.first)
    assert organization is not None
    db.add(
        OrganizationRoleGrant(
            org_id=organization.id,
            tenant_id=organization.tenant_id,
            principal_type="user",
            principal_id=org_fixture.user.id,
            role="admin",
            granted_by=org_fixture.user.id,
        )
    )
    await db.commit()
    audit_before = await db.scalar(select(func.count()).select_from(AuditEvent))
    pending = Tenant(
        name=f"Pending route {actor_source}",
        slug=f"b04-pending-route-{actor_source}-{uuid4().hex}",
        secrets_namespace=f"b04/pending/route/{actor_source}/{uuid4().hex}",
    )
    db.add(pending)
    flushes = 0

    def count_flushes(*_args) -> None:
        nonlocal flushes
        flushes += 1

    async def request_db() -> AsyncGenerator[AsyncSession, None]:
        yield db

    async def current_actor() -> CurrentUser:
        return org_fixture.user

    original_db = raw_test_app.dependency_overrides[get_db]
    raw_test_app.dependency_overrides[get_db] = request_db
    if actor_source == "current_user_override":
        raw_test_app.dependency_overrides[get_current_user] = current_actor
    event.listen(db.sync_session, "before_flush", count_flushes)
    try:
        denied = await raw_client.post(
            f"/api/orgs/{organization.id}/members",
            json={"user_id": str(org_fixture.user.id)},
            headers=org_fixture.session.headers(),
        )
    finally:
        event.remove(db.sync_session, "before_flush", count_flushes)
        raw_test_app.dependency_overrides[get_db] = original_db
        if actor_source == "current_user_override":
            raw_test_app.dependency_overrides.pop(get_current_user)

    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "stale_version"
    with db.no_autoflush:
        pending_visible = await db.scalar(select(Tenant.id).where(Tenant.id == pending.id))
        audit_after = await db.scalar(select(func.count()).select_from(AuditEvent))
    assert flushes == 0
    assert pending_visible is None and pending in db.new
    assert audit_after == audit_before
