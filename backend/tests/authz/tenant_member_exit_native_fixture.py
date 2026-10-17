"""Shared PostgreSQL fixture support for tenant-member exit integration tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from fastapi import Request
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.dependencies import CurrentUser
from app.models.audit_event import AuditEvent
from app.models.organization import (
    Organization,
    OrganizationMember,
    Tenant,
    TenantMember,
    TenantRoleGrant,
)
from app.models.organization_group import OrgInvitation
from app.models.skill import Skill
from app.models.user import User
from app.services.tenants.member_exit import TenantMemberExitRequest, exit_tenant_member
from tests.authz.company_administration_migration_fixture import _runner_dsns


@dataclass(frozen=True, slots=True)
class NativeCompany:
    tenant_id: UUID
    actor: CurrentUser
    target_id: UUID
    target_email: str
    organizations: tuple[UUID, ...]


def audit_request() -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/native-fixture",
            "raw_path": b"/native-fixture",
            "query_string": b"",
            "headers": [],
            "client": None,
            "server": ("test", 80),
            "state": {},
        }
    )


async def seed_company(
    db: AsyncSession, *, org_count: int = 3, membership_count: int | None = None
) -> NativeCompany:
    key = uuid4().hex
    actor = User(email=f"native-admin-{key}@example.test", name="Native admin")
    target = User(email=f"native-target-{key}@example.test", name="Native target")
    tenant = Tenant(name="Native company", slug=f"native-{key}", secrets_namespace=f"native/{key}")
    db.add_all([actor, target, tenant])
    await db.flush()
    organizations = tuple(
        Organization(tenant_id=tenant.id, name=f"Native {index}", slug=f"native-{key}-{index}")
        for index in range(org_count)
    )
    db.add_all(organizations)
    await db.flush()
    db.add_all(
        [
            TenantMember(tenant_id=tenant.id, user_id=actor.id),
            TenantMember(tenant_id=tenant.id, user_id=target.id),
            TenantRoleGrant(
                tenant_id=tenant.id,
                user_id=actor.id,
                role="admin",
                granted_by=actor.id,
            ),
        ]
    )
    db.add_all(
        OrganizationMember(org_id=organization.id, tenant_id=tenant.id, user_id=target.id)
        for organization in organizations[: membership_count or org_count]
    )
    await db.commit()
    return NativeCompany(
        tenant.id,
        CurrentUser(id=actor.id, email=actor.email, name=actor.name, is_super_user=False),
        target.id,
        target.email,
        tuple(organization.id for organization in organizations),
    )


async def remove_company(
    sessions: async_sessionmaker[AsyncSession], fixture: NativeCompany
) -> None:
    async with sessions() as db:
        await exit_tenant_member(
            TenantMemberExitRequest(db, fixture.tenant_id, fixture.target_id, fixture.actor)
        )
        await db.commit()


async def assert_removed(
    sessions: async_sessionmaker[AsyncSession], fixture: NativeCompany
) -> None:
    async with sessions() as db:
        company = await db.get(TenantMember, (fixture.tenant_id, fixture.target_id))
        active_orgs = await db.scalar(
            select(func.count())
            .select_from(OrganizationMember)
            .where(
                OrganizationMember.tenant_id == fixture.tenant_id,
                OrganizationMember.user_id == fixture.target_id,
                OrganizationMember.status == "active",
            )
        )
        roles = await db.scalar(
            select(func.count())
            .select_from(TenantRoleGrant)
            .where(
                TenantRoleGrant.tenant_id == fixture.tenant_id,
                TenantRoleGrant.user_id == fixture.target_id,
            )
        )
        assert company is not None and company.status == "removed"
        assert active_orgs == 0
        assert roles == 0


async def cleanup_company(
    sessions: async_sessionmaker[AsyncSession],
    fixture: NativeCompany,
    *,
    call_phase_complete: bool | None = None,
) -> None:
    try:
        async with sessions() as db:
            # Company/org audit rows reference the company and its organizations (RESTRICT).
            await db.execute(delete(AuditEvent).where(AuditEvent.tenant_id == fixture.tenant_id))
            await db.execute(delete(Skill).where(Skill.tenant_id == fixture.tenant_id))
            await db.execute(
                delete(OrgInvitation).where(OrgInvitation.tenant_id == fixture.tenant_id)
            )
            await db.execute(
                delete(Organization).where(Organization.tenant_id == fixture.tenant_id)
            )
            await db.execute(delete(Tenant).where(Tenant.id == fixture.tenant_id))
            await db.execute(delete(User).where(User.id.in_((fixture.actor.id, fixture.target_id))))
            await db.commit()
    except IntegrityError as error:
        raise AssertionError(
            "native_company_fixture_cleanup_integrity_error:"
            f"call_phase_complete={call_phase_complete}"
        ) from error


@pytest.fixture
async def native_sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(_runner_dsns().async_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
