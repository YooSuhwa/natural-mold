"""Cross-transaction suspension and removal invalidate previously loaded company rows."""

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.organization import Tenant, TenantMember, TenantRoleGrant
from app.models.user import User
from app.tenancy.context import resolve_tenant_context
from tests.authz.organization_migration_fixture import async_organization_lab

pytestmark = pytest.mark.integration


async def test_other_transaction_status_and_membership_changes_are_fresh_in_same_session() -> None:
    async with async_organization_lab() as lab:
        engine = create_async_engine(lab.engine.url.set(drivername="postgresql+asyncpg"))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as db:
                user = User(email="tenant-fresh@example.test", name="Company admin")
                tenant = Tenant(name="Fresh", slug="fresh", secrets_namespace="fresh")
                db.add_all([user, tenant])
                await db.flush()
                db.add(TenantMember(tenant_id=tenant.id, user_id=user.id))
                db.add(TenantRoleGrant(tenant_id=tenant.id, user_id=user.id, role="admin"))
                await db.commit()
                actor = CurrentUser(id=user.id, email=user.email, name=user.name)
                assert (await resolve_tenant_context(db, actor, tenant.id)).roles == ("admin",)
                # Given: this long-lived session already has the active company in its identity map.
                assert tenant.status == "active"
                async with sessions() as other:
                    await other.execute(
                        sa.update(Tenant).where(Tenant.id == tenant.id).values(status="suspended")
                    )
                    await other.commit()
                # When: another transaction suspends it after the context's first resolution.
                with pytest.raises(AppError) as suspended:
                    await resolve_tenant_context(db, actor, tenant.id)
                assert (suspended.value.status, suspended.value.code) == (403, "tenant_suspended")
                async with sessions() as other:
                    await other.execute(
                        sa.update(Tenant).where(Tenant.id == tenant.id).values(status="active")
                    )
                    await other.commit()
                assert (await resolve_tenant_context(db, actor, tenant.id)).tenant_id == tenant.id
                async with sessions() as other:
                    await other.execute(
                        sa.update(TenantMember)
                        .where(TenantMember.tenant_id == tenant.id)
                        .values(status="removed")
                    )
                    await other.commit()
                # Then: membership removal is denied despite the surviving role and ORM object.
                with pytest.raises(AppError) as removed:
                    await resolve_tenant_context(db, actor, tenant.id)
                assert removed.value.status == 404
        finally:
            await engine.dispose()
