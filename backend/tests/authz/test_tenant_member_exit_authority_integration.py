"""PostgreSQL authority and denial coverage for company member exit."""

from __future__ import annotations

from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.audit_event import AuditEvent
from app.models.organization import Tenant, TenantMember, TenantRoleGrant
from app.services.tenants.member_exit import TenantMemberExitRequest, exit_tenant_member
from tests.authz.tenant_member_exit_native_fixture import cleanup_company, seed_company

pytestmark = pytest.mark.integration
pytest_plugins = ["tests.authz.tenant_member_exit_native_fixture"]


async def test_native_company_exit_reloads_cached_role_after_committed_revocation(
    native_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with native_sessions() as db:
        fixture = await seed_company(db)
    try:
        async with native_sessions() as cached_db:
            cached = await cached_db.scalar(
                select(TenantRoleGrant).where(
                    TenantRoleGrant.tenant_id == fixture.tenant_id,
                    TenantRoleGrant.user_id == fixture.actor.id,
                )
            )
            assert cached is not None and cached.role == "admin"
            await cached_db.commit()
            async with native_sessions() as revoker:
                await revoker.execute(
                    update(TenantRoleGrant)
                    .where(TenantRoleGrant.id == cached.id)
                    .values(role="auditor")
                    .execution_options(synchronize_session=False)
                )
                await revoker.commit()

            with pytest.raises(AppError) as captured:
                await exit_tenant_member(
                    TenantMemberExitRequest(
                        cached_db, fixture.tenant_id, fixture.target_id, fixture.actor
                    )
                )
            assert (captured.value.status, captured.value.code) == (
                403,
                "permission_required",
            )
            assert cached.role == "auditor"
            await cached_db.rollback()

        async with native_sessions() as verification:
            member = await verification.get(TenantMember, (fixture.tenant_id, fixture.target_id))
            assert member is not None and member.status == "active"
            assert (
                await verification.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.target_id == str(fixture.target_id),
                        AuditEvent.action.in_(("org.member_remove", "tenant.member_remove")),
                    )
                )
                == 0
            )
    finally:
        await cleanup_company(native_sessions, fixture)


async def test_native_company_exit_rejects_suspended_company(
    native_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with native_sessions() as db:
        fixture = await seed_company(db)
        await db.execute(
            update(Tenant).where(Tenant.id == fixture.tenant_id).values(status="suspended")
        )
        await db.commit()
    try:
        async with native_sessions() as db:
            with pytest.raises(AppError) as captured:
                await exit_tenant_member(
                    TenantMemberExitRequest(db, fixture.tenant_id, fixture.target_id, fixture.actor)
                )
            assert (captured.value.status, captured.value.code) == (
                403,
                "tenant_suspended",
            )
            await db.rollback()
        async with native_sessions() as verification:
            member = await verification.get(TenantMember, (fixture.tenant_id, fixture.target_id))
            assert member is not None and member.status == "active"
            assert (
                await verification.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.target_id == str(fixture.target_id),
                        AuditEvent.action.in_(("org.member_remove", "tenant.member_remove")),
                    )
                )
                == 0
            )
    finally:
        await cleanup_company(native_sessions, fixture)


async def test_native_denied_company_exit_preserves_unrelated_pending_tenant(
    native_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with native_sessions() as db:
        fixture = await seed_company(db)
    try:
        async with native_sessions() as db:
            pending = Tenant(
                name="Native pending company",
                slug=f"native-pending-{uuid4().hex}",
                secrets_namespace=f"native/pending/{uuid4().hex}",
            )
            db.add(pending)
            flush = Mock()
            event.listen(db.sync_session, "before_flush", flush)
            target_actor = CurrentUser(
                id=fixture.target_id,
                email=fixture.target_email,
                name="Native target",
                is_super_user=False,
            )
            try:
                with pytest.raises(AppError) as captured:
                    await exit_tenant_member(
                        TenantMemberExitRequest(
                            db, fixture.tenant_id, fixture.target_id, target_actor
                        )
                    )
            finally:
                event.remove(db.sync_session, "before_flush", flush)
            with db.no_autoflush:
                persisted = await db.scalar(select(Tenant.id).where(Tenant.slug == pending.slug))
            assert (captured.value.status, captured.value.code) == (
                403,
                "permission_required",
            )
            assert flush.call_count == 0
            assert pending in db.new
            assert persisted is None
            await db.rollback()
    finally:
        await cleanup_company(native_sessions, fixture)
