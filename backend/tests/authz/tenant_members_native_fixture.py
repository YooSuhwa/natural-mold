"""Owned PostgreSQL/HTTP fixtures for final company-administrator races."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import anyio
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, or_, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.auth.password import hash_password
from app.dependencies import get_db
from app.main import create_app
from app.models.audit_event import AuditEvent
from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRoleGrant,
    Tenant,
    TenantMember,
    TenantRoleGrant,
)
from app.models.organization_group import OrgInvitation
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.routers.tenant_members import router
from app.services.tenants.versioning import etag
from tests.authz.company_administration_migration_fixture import _runner_dsns

PASSWORD = "task8-native-password"
APPLICATIONS = ("task8_member_first", "task8_member_second", "task8_member_observer")


@dataclass(frozen=True, slots=True)
class NativeSessions:
    first: async_sessionmaker[AsyncSession]
    second: async_sessionmaker[AsyncSession]
    observer: async_sessionmaker[AsyncSession]
    owned_tenant_ids: set[UUID] = field(default_factory=set)
    owned_user_ids: set[UUID] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class NativeActor:
    id: UUID
    email: str


@dataclass(frozen=True, slots=True)
class NativeMemberRace:
    tenant_id: UUID
    actors: tuple[NativeActor, NativeActor]
    organization_ids: tuple[UUID, UUID]
    initial_etag: str


def _engine(name: str) -> AsyncEngine:
    return create_async_engine(
        _runner_dsns().async_url,
        connect_args={"server_settings": {"application_name": name}},
    )


@pytest.fixture
async def tenant_member_native_sessions() -> AsyncIterator[NativeSessions]:
    engines = tuple(_engine(name) for name in APPLICATIONS)
    sessionmakers = tuple(async_sessionmaker(engine, expire_on_commit=False) for engine in engines)
    sessions = NativeSessions(
        first=sessionmakers[0], second=sessionmakers[1], observer=sessionmakers[2]
    )
    try:
        yield sessions
    finally:
        await cleanup_owned_rows(sessions)
        for engine in engines:
            await engine.dispose()


def _app(sessions: async_sessionmaker[AsyncSession]) -> FastAPI:
    app = create_app()
    app.include_router(router)

    async def database_session() -> AsyncGenerator[AsyncSession, None]:
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database_session
    return app


@asynccontextmanager
async def native_clients(
    sessions: NativeSessions,
) -> AsyncGenerator[tuple[AsyncClient, AsyncClient], None]:
    first_app, second_app = _app(sessions.first), _app(sessions.second)
    async with (
        AsyncClient(transport=ASGITransport(app=first_app), base_url="http://test") as first,
        AsyncClient(transport=ASGITransport(app=second_app), base_url="http://test") as second,
    ):
        yield first, second


async def seed_admin_race(sessions: NativeSessions) -> NativeMemberRace:
    key = uuid4().hex
    user_ids = (uuid4(), uuid4())
    users = tuple(
        User(
            id=user_ids[index],
            email=f"task8-native-{index}-{key}@example.com",
            name=f"Task8 admin {index}",
            hashed_password=hash_password(PASSWORD),
            is_active=True,
        )
        for index in range(2)
    )
    tenant = Tenant(
        id=uuid4(),
        name="Task8 native",
        slug=f"task8-native-{key}",
        secrets_namespace=f"task8/{key}",
    )
    sessions.owned_user_ids.update(user_ids)
    sessions.owned_tenant_ids.add(tenant.id)
    async with sessions.observer() as db:
        db.add_all((*users, tenant))
        await db.flush()
        organizations = tuple(
            Organization(tenant_id=tenant.id, name=f"Task8 org {i}", slug=f"task8-{key}-{i}")
            for i in range(2)
        )
        db.add_all(organizations)
        await db.flush()
        for user in users:
            db.add(TenantMember(tenant_id=tenant.id, user_id=user.id, status="active"))
            db.add(TenantRoleGrant(tenant_id=tenant.id, user_id=user.id, role="admin"))
            for organization in organizations:
                db.add(
                    OrganizationMember(org_id=organization.id, tenant_id=tenant.id, user_id=user.id)
                )
                db.add(
                    OrganizationRoleGrant(
                        org_id=organization.id,
                        tenant_id=tenant.id,
                        principal_type="user",
                        principal_id=user.id,
                        role="admin",
                    )
                )
        await db.commit()
        await db.refresh(tenant)
    return NativeMemberRace(
        tenant.id,
        (
            NativeActor(users[0].id, users[0].email),
            NativeActor(users[1].id, users[1].email),
        ),
        (organizations[0].id, organizations[1].id),
        etag(tenant.updated_at),
    )


async def login(client: AsyncClient, actor: NativeActor) -> str:
    response = await client.post(
        "/api/auth/login", json={"email": actor.email, "password": PASSWORD}
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


async def assert_blocked(sessions: NativeSessions, blocked_pid: int, blocker_pid: int) -> None:
    query = text(
        "SELECT pg_blocking_pids(pid), wait_event_type FROM pg_stat_activity WHERE pid=:pid"
    )
    with anyio.fail_after(5):
        while True:
            async with sessions.observer() as db:
                row = (await db.execute(query, {"pid": blocked_pid})).one()
            if blocker_pid in row[0] and row[1] == "Lock":
                return
            await anyio.sleep(0.01)


async def cleanup_owned_rows(sessions: NativeSessions) -> None:
    async with sessions.observer() as db:
        user_ids = tuple(sessions.owned_user_ids)
        tenant_ids = tuple(sessions.owned_tenant_ids)
        if user_ids:
            target_ids = tuple(str(value) for value in (*user_ids, *tenant_ids))
            await db.execute(
                delete(AuditEvent).where(
                    or_(
                        AuditEvent.actor_user_id.in_(user_ids),
                        AuditEvent.owner_user_id.in_(user_ids),
                        AuditEvent.target_owner_user_id.in_(user_ids),
                        AuditEvent.target_id.in_(target_ids),
                    )
                )
            )
            await db.execute(delete(RefreshToken).where(RefreshToken.user_id.in_(user_ids)))
        if tenant_ids:
            await db.execute(delete(OrgInvitation).where(OrgInvitation.tenant_id.in_(tenant_ids)))
            await db.execute(
                delete(OrganizationRoleGrant).where(OrganizationRoleGrant.tenant_id.in_(tenant_ids))
            )
            await db.execute(
                delete(OrganizationMember).where(OrganizationMember.tenant_id.in_(tenant_ids))
            )
            await db.execute(
                delete(TenantRoleGrant).where(TenantRoleGrant.tenant_id.in_(tenant_ids))
            )
            await db.execute(delete(TenantMember).where(TenantMember.tenant_id.in_(tenant_ids)))
            await db.execute(delete(Organization).where(Organization.tenant_id.in_(tenant_ids)))
            await db.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
        if user_ids:
            await db.execute(delete(User).where(User.id.in_(user_ids)))
        await db.commit()
