"""Development bootstrap is explicitly scoped and serialized on actual B11."""

from uuid import UUID

import anyio
import pytest
from anyio.to_thread import run_sync
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from app.authz.org_context import resolve_org_context
from app.config import settings
from app.dependencies import CurrentUser
from app.models.organization import OrganizationMember, OrganizationRoleGrant, TenantRoleGrant
from app.models.user import User
from app.seed.e2e_scope import E2E_ORG_ID, E2E_TENANT_ID
from app.seed.e2e_user import seed_e2e_user
from tests.authz.organization_migration_fixture import async_organization_lab

pytestmark = pytest.mark.integration


async def test_concurrent_seed_creates_one_explicit_development_membership_on_b11(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: two development backends share a freshly migrated, owned test database.
    monkeypatch.setattr(settings, "app_env", "dev")
    monkeypatch.setattr(settings, "e2e_seed_user_enabled", True)
    monkeypatch.setattr(settings, "e2e_user_email", "native-e2e-scope@example.test")
    monkeypatch.setattr(settings, "e2e_user_password", "native-fixture-password")
    async with async_organization_lab() as lab:
        await run_sync(command.upgrade, lab.config, "b11_company_administration")
        engine = create_async_engine(lab.engine.url.set(drivername="postgresql+asyncpg"))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        seeded: list[UUID] = []

        async def seed_once() -> None:
            async with sessions() as db:
                user = await seed_e2e_user(db)
                assert user is not None
                await db.commit()
                seeded.append(user.id)

        try:
            # When: both seed transactions attempt to create account and org enrollment.
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(seed_once)
                tasks.start_soon(seed_once)
            # Then: both resolve the same explicitly enrolled account without duplicates.
            assert len(seeded) == 2 and seeded[0] == seeded[1]
            async with sessions() as db:
                assert await db.scalar(select(func.count()).select_from(User)) == 1
                membership = await db.get(OrganizationMember, (E2E_ORG_ID, seeded[0]))
                assert membership is not None and membership.tenant_id == E2E_TENANT_ID
                assert await db.scalar(select(func.count()).select_from(TenantRoleGrant)) == 1
                assert await db.scalar(select(func.count()).select_from(OrganizationRoleGrant)) == 1
                user = await db.get(User, seeded[0])
                assert user is not None
                actor = CurrentUser(
                    id=user.id, email=user.email, name=user.name, is_super_user=user.is_super_user
                )
                context = await resolve_org_context(db, actor, cookie=None)
                assert (context.tenant_id, context.org_id) == (E2E_TENANT_ID, E2E_ORG_ID)
        finally:
            await engine.dispose()
