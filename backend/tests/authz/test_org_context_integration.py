"""Real PostgreSQL, JWT/CSRF cookies and a protected request after membership removal."""

from collections.abc import AsyncGenerator

import anyio
import pytest
from anyio.to_thread import run_sync
from fastapi import Depends
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.authz.org_context import OrgContext
from app.authz.policies import SELF, authz_policy
from app.dependencies import get_db, get_org_context
from app.main import create_app
from app.models.organization import Organization, OrganizationMember, Tenant, TenantMember
from app.schemas.auth import UserResponse
from app.schemas.organization import ActiveOrganizationResponse
from tests.authz.organization_migration_fixture import async_organization_lab
from tests.conftest import register_session

pytestmark = pytest.mark.integration


async def test_postgres_org_switch_and_removed_membership_block_next_request() -> None:
    # Given: the actual B11 schema in an owned disposable database and real auth routes.
    async with async_organization_lab() as lab:
        await run_sync(command.upgrade, lab.config, "head")
        engine = create_async_engine(lab.engine.url.set(drivername="postgresql+asyncpg"))
        try:
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            app = create_app()

            async def database_session() -> AsyncGenerator[AsyncSession, None]:
                async with sessions() as db:
                    yield db

            app.dependency_overrides[get_db] = database_session

            @app.get("/test/org-context")
            @authz_policy(SELF)
            async def protected_context(
                context: OrgContext = Depends(get_org_context),
            ) -> ActiveOrganizationResponse:
                return ActiveOrganizationResponse.model_validate(context)

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                login = await register_session(
                    client, email="pg-context@example.com", name="PG context"
                )
                user_id = UserResponse.model_validate(login.body["user"]).id
                tenant = Tenant(name="PG", slug="pg", secrets_namespace="pg")
                foreign = Tenant(name="Foreign", slug="foreign", secrets_namespace="foreign")
                async with sessions() as db, db.begin():
                    db.add_all([tenant, foreign])
                    await db.flush()
                    org = Organization(tenant_id=tenant.id, name="PG", slug="pg")
                    outside = Organization(tenant_id=foreign.id, name="Foreign", slug="foreign")
                    db.add_all([org, outside])
                    await db.flush()
                    db.add(TenantMember(tenant_id=tenant.id, user_id=user_id))
                    db.add(OrganizationMember(org_id=org.id, tenant_id=tenant.id, user_id=user_id))
                # When: a real browser cookie flow selects a company and later loses membership.
                denied = await client.post(
                    "/api/me/active-org",
                    json={"org_id": str(outside.id)},
                    headers=login.headers(),
                )
                assert denied.status_code == 404
                selected = await client.post(
                    "/api/me/active-org",
                    json={"org_id": str(org.id)},
                    headers=login.headers(),
                )
                assert selected.status_code == 200
                active = await client.get("/test/org-context")
                assert active.status_code == 200 and active.json()["tenant_id"] == str(tenant.id)
                async with sessions() as db, db.begin():
                    member = await db.get(OrganizationMember, (org.id, user_id))
                    assert member is not None
                    member.status = "removed"
                removed = await client.get("/test/org-context")
                # Then: an unchanged valid JWT/current-org cookie cannot retain the removed context.
                assert removed.status_code == 403
                assert removed.json()["error"]["code"] == "forbidden"
        finally:
            with anyio.CancelScope(shield=True):
                await engine.dispose()
