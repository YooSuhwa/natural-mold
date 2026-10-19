"""PostgreSQL add-member races against company member exit."""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

import anyio
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.authz import org_control
from app.authz.org_control import OrgControlContext
from app.authz.tenant_org_write_locks import TenantOrgWriteLock
from app.exceptions import AppError
from app.models.audit_event import AuditEvent
from app.models.organization import Organization
from app.schemas.org_members import MemberCreate
from app.services.orgs.info import OrgMutationRequest
from app.services.orgs.members import create_member
from app.services.orgs.versioning import etag
from app.services.tenants import member_exit
from tests.authz.tenant_member_exit_native_fixture import (
    assert_removed,
    audit_request,
    cleanup_company,
    remove_company,
    seed_company,
)

pytestmark = pytest.mark.integration
pytest_plugins = ["tests.authz.tenant_member_exit_native_fixture"]


async def test_company_exit_serializes_with_add_in_initially_unaffected_organization(
    native_sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with native_sessions() as db:
        fixture = await seed_company(db, org_count=2, membership_count=1)
    organization_id = fixture.organizations[1]
    original = org_control.lock_tenant_org_write
    locked, release, add_done = anyio.Event(), anyio.Event(), anyio.Event()

    async def hold_add_lock(
        db: AsyncSession, tenant_id: UUID, organization_ids: Collection[UUID] | None
    ) -> TenantOrgWriteLock:
        result = await original(db, tenant_id, organization_ids)
        locked.set()
        await release.wait()
        return result

    monkeypatch.setattr(org_control, "lock_tenant_org_write", hold_add_lock)

    async def add_member() -> None:
        async with native_sessions() as db:
            organization = await db.get(Organization, organization_id)
            assert organization is not None
            context = OrgControlContext(organization, fixture.actor, ("can_manage_members",))
            mutation = OrgMutationRequest(db, audit_request(), etag(organization.updated_at))
            await create_member(mutation, context, MemberCreate(user_id=fixture.target_id))
        add_done.set()

    try:
        with anyio.fail_after(10):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(add_member)
                await locked.wait()
                tasks.start_soon(remove_company, native_sessions, fixture)
                await anyio.sleep(0.1)
                assert not add_done.is_set()
                release.set()
        await assert_removed(native_sessions, fixture)
    finally:
        await cleanup_company(native_sessions, fixture)


async def test_company_exit_wins_before_add_in_initially_unaffected_organization(
    native_sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with native_sessions() as db:
        fixture = await seed_company(db, org_count=2, membership_count=1)
    organization_id = fixture.organizations[1]
    original = member_exit.lock_tenant_org_write
    locked, release = anyio.Event(), anyio.Event()
    add_error: list[str] = []

    async def hold_removal_lock(
        db: AsyncSession, tenant_id: UUID, organization_ids: Collection[UUID] | None
    ) -> TenantOrgWriteLock:
        result = await original(db, tenant_id, organization_ids)
        locked.set()
        await release.wait()
        return result

    monkeypatch.setattr(member_exit, "lock_tenant_org_write", hold_removal_lock)

    async def add_member() -> None:
        async with native_sessions() as db:
            organization = await db.get(Organization, organization_id)
            assert organization is not None
            context = OrgControlContext(organization, fixture.actor, ("can_manage_members",))
            mutation = OrgMutationRequest(db, audit_request(), etag(organization.updated_at))
            try:
                await create_member(mutation, context, MemberCreate(user_id=fixture.target_id))
            except AppError as error:
                add_error.append(error.code)
                await db.rollback()

    try:
        with anyio.fail_after(10):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(remove_company, native_sessions, fixture)
                await locked.wait()
                tasks.start_soon(add_member)
                await anyio.sleep(0.1)
                release.set()
        assert add_error == ["not_found"]
        await assert_removed(native_sessions, fixture)
        async with native_sessions() as db:
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.target_id == str(fixture.target_id),
                        AuditEvent.action == "org.member_add",
                        AuditEvent.event_metadata["org_id"].as_string() == str(organization_id),
                    )
                )
                == 0
            )
    finally:
        await cleanup_company(native_sessions, fixture)
