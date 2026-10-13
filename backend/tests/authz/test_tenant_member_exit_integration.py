"""PostgreSQL core and race coverage for company member exit."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.authz.org_member_exit import MemberExitPlan
from app.exceptions import AppError
from app.models.audit_event import AuditEvent
from app.models.organization import (
    OrganizationMember,
)
from app.models.skill import Skill
from app.services.tenants import member_exit
from app.services.tenants.member_exit import TenantMemberExitRequest, exit_tenant_member
from tests.authz.tenant_member_exit_native_fixture import (
    assert_removed,
    cleanup_company,
    remove_company,
    seed_company,
)

pytestmark = pytest.mark.integration
pytest_plugins = ["tests.authz.tenant_member_exit_native_fixture"]


async def test_native_valid_company_exit_removes_all_organizations(
    native_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with native_sessions() as db:
        fixture = await seed_company(db)
    try:
        await remove_company(native_sessions, fixture)
        await assert_removed(native_sessions, fixture)
    finally:
        await cleanup_company(native_sessions, fixture)


async def test_native_late_ownership_conflict_leaves_company_memberships_unchanged(
    native_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with native_sessions() as db:
        fixture = await seed_company(db)
        db.add(
            Skill(
                user_id=fixture.target_id,
                name="Native owned skill",
                slug=f"native-owned-{uuid4().hex}",
                tenant_id=fixture.tenant_id,
                org_id=sorted(fixture.organizations, key=lambda value: value.bytes)[-1],
            )
        )
        await db.commit()
    try:
        async with native_sessions() as db:
            with pytest.raises(AppError) as captured:
                await exit_tenant_member(
                    TenantMemberExitRequest(db, fixture.tenant_id, fixture.target_id, fixture.actor)
                )
            assert captured.value.code == "resource_transfer_required"
            assert captured.value.status == 409
            await db.rollback()
        async with native_sessions() as db:
            statuses = list(
                await db.scalars(
                    select(OrganizationMember.status).where(
                        OrganizationMember.tenant_id == fixture.tenant_id,
                        OrganizationMember.user_id == fixture.target_id,
                    )
                )
            )
            assert len(statuses) == 3
            assert set(statuses) == {"active"}
            assert (
                await db.scalar(
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


async def test_native_late_apply_failure_rolls_back_every_company_exit_write(
    native_sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with native_sessions() as db:
        fixture = await seed_company(db)
    original = member_exit.apply_member_exit
    calls = 0

    async def fail_second(db: AsyncSession, plan: MemberExitPlan) -> None:
        nonlocal calls
        calls += 1
        await original(db, plan)
        if calls == 2:
            raise RuntimeError("native injected late failure")

    monkeypatch.setattr(member_exit, "apply_member_exit", fail_second)
    try:
        async with native_sessions() as db:
            with pytest.raises(RuntimeError, match="native injected late failure"):
                await exit_tenant_member(
                    TenantMemberExitRequest(db, fixture.tenant_id, fixture.target_id, fixture.actor)
                )
            await db.rollback()
        async with native_sessions() as db:
            statuses = list(
                await db.scalars(
                    select(OrganizationMember.status).where(
                        OrganizationMember.tenant_id == fixture.tenant_id,
                        OrganizationMember.user_id == fixture.target_id,
                    )
                )
            )
            assert len(statuses) == 3
            assert set(statuses) == {"active"}
    finally:
        await cleanup_company(native_sessions, fixture)
