"""Actual-router SQLite fixtures for Task 7 organization administration."""

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.main import create_app
from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRoleGrant,
    Tenant,
    TenantMember,
    TenantRoleGrant,
)
from app.models.user import User
from app.routers.tenant_organizations import router
from tests import conftest as shared
from tests.authz.registration_fixture import clear_default_membership, seed_registration_default
from tests.conftest import AuthSession, register_session


@dataclass(frozen=True, slots=True)
class Task7Case:
    auth: AuthSession
    actor_id: UUID
    target_id: UUID
    tenant_id: UUID
    org_id: UUID


@pytest.fixture
async def task7_client() -> AsyncGenerator[AsyncClient, None]:
    app = create_app()
    collection = "/api/tenants/{tenant_id}/organizations"
    if not any(isinstance(route, APIRoute) and route.path == collection for route in app.routes):
        app.include_router(router)
    app.dependency_overrides[get_db] = shared.override_get_db
    async with shared.TestSession() as db:
        await seed_registration_default(db)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


async def seed_task7_case(
    client: AsyncClient,
    db: AsyncSession,
    *,
    access: Literal["admin", "auditor"] = "admin",
) -> Task7Case:
    key = uuid4().hex
    auth = await register_session(client, email=f"task7-{key}@example.com", name="Task 7 actor")
    actor = await db.scalar(select(User).where(User.email == f"task7-{key}@example.com"))
    assert actor is not None
    await clear_default_membership(db, actor.id)
    target = User(email=f"task7-target-{key}@example.com", name="Task 7 target")
    tenant = Tenant(
        name="Task 7 company",
        slug=f"task7-{key}",
        secrets_namespace=f"task7/{key}",
    )
    db.add_all((target, tenant))
    await db.flush()
    organization = Organization(
        tenant_id=tenant.id,
        name="Existing organization",
        slug=f"existing-{key}",
        settings={"default_member_role": "member", "preserved": True},
    )
    db.add(organization)
    await db.flush()
    db.add_all(
        (
            TenantMember(tenant_id=tenant.id, user_id=actor.id),
            TenantMember(tenant_id=tenant.id, user_id=target.id),
            TenantRoleGrant(
                tenant_id=tenant.id,
                user_id=actor.id,
                role=access,
                granted_by=actor.id,
            ),
            OrganizationMember(
                org_id=organization.id,
                tenant_id=tenant.id,
                user_id=target.id,
            ),
            OrganizationRoleGrant(
                org_id=organization.id,
                tenant_id=tenant.id,
                principal_type="user",
                principal_id=target.id,
                role="admin",
                granted_by=actor.id,
            ),
        )
    )
    await db.commit()
    auth.apply(client)
    return Task7Case(auth, actor.id, target.id, tenant.id, organization.id)
