"""Canonical PostgreSQL resources for atomic company creation acceptance."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.auth.jwt import create_access_token, create_csrf_token
from app.config import settings
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
from app.models.tenant_invitation import TenantInvitation
from app.models.user import User
from app.routers.platform_tenant_creation import router
from tests.authz.company_administration_migration_fixture import _runner_dsns


@dataclass(frozen=True, slots=True)
class NativeCreationDatabase:
    primary: async_sessionmaker[AsyncSession]
    competing: async_sessionmaker[AsyncSession]
    observer: async_sessionmaker[AsyncSession]

    @asynccontextmanager
    async def client(
        self, sessions: async_sessionmaker[AsyncSession], actor_id: UUID
    ) -> AsyncIterator[AsyncClient]:
        async def database_session() -> AsyncGenerator[AsyncSession, None]:
            async with sessions() as db:
                yield db

        app = create_app()
        app.include_router(router)
        app.dependency_overrides[get_db] = database_session
        csrf = create_csrf_token(actor_id)
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://native-test",
        ) as client:
            client.cookies.set(
                settings.cookie_name_access,
                create_access_token(actor_id, is_super_user=True),
            )
            client.cookies.set(settings.cookie_name_csrf, csrf)
            client.headers["X-CSRF-Token"] = csrf
            yield client


@dataclass(frozen=True, slots=True)
class NativeOwnedCreation:
    actor_id: UUID
    tenant_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class NativeCreationFacts:
    tenants: int
    organizations: int
    invitations: int
    audits: int
    tenant_members: int
    tenant_roles: int
    organization_members: int
    organization_roles: int


def _engine(name: str) -> AsyncEngine:
    return create_async_engine(
        _runner_dsns().async_url,
        connect_args={"server_settings": {"application_name": name}},
        pool_size=2,
        max_overflow=0,
    )


@pytest.fixture
async def native_creation_database(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[NativeCreationDatabase]:
    monkeypatch.setattr(settings, "deployment_mode", "multi_tenant")
    assert settings.deployment_mode == "multi_tenant"
    engines = (
        _engine("task12_primary"),
        _engine("task12_competing"),
        _engine("task12_observer"),
    )
    factories = tuple(async_sessionmaker(engine, expire_on_commit=False) for engine in engines)
    try:
        yield NativeCreationDatabase(factories[0], factories[1], factories[2])
    finally:
        for engine in engines:
            await engine.dispose()


@asynccontextmanager
async def native_owned_creation(
    database: NativeCreationDatabase,
    *,
    actor_id: UUID,
    tenant_ids: tuple[UUID, ...],
) -> AsyncIterator[NativeOwnedCreation]:
    owned = NativeOwnedCreation(actor_id, tenant_ids)
    try:
        async with database.observer() as db:
            db.add(
                User(
                    id=actor_id,
                    email=f"task12-native-{actor_id.hex}@example.com",
                    name="Task 12 Native Operator",
                    hashed_password="unused",
                    is_active=True,
                    is_super_user=True,
                )
            )
            await db.commit()
        yield owned
    finally:
        await cleanup_owned_creation(database.observer, owned)


async def creation_facts(
    sessions: async_sessionmaker[AsyncSession], tenant_id: UUID
) -> NativeCreationFacts:
    async with sessions() as db:
        organization_ids = select(Organization.id).where(Organization.tenant_id == tenant_id)
        counts = (
            await db.scalar(select(func.count()).select_from(Tenant).where(Tenant.id == tenant_id)),
            await db.scalar(
                select(func.count())
                .select_from(Organization)
                .where(Organization.tenant_id == tenant_id)
            ),
            await db.scalar(
                select(func.count())
                .select_from(TenantInvitation)
                .where(TenantInvitation.tenant_id == tenant_id)
            ),
            await db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "tenant.create", AuditEvent.target_id == str(tenant_id))
            ),
            await db.scalar(
                select(func.count())
                .select_from(TenantMember)
                .where(TenantMember.tenant_id == tenant_id)
            ),
            await db.scalar(
                select(func.count())
                .select_from(TenantRoleGrant)
                .where(TenantRoleGrant.tenant_id == tenant_id)
            ),
            await db.scalar(
                select(func.count())
                .select_from(OrganizationMember)
                .where(OrganizationMember.org_id.in_(organization_ids))
            ),
            await db.scalar(
                select(func.count())
                .select_from(OrganizationRoleGrant)
                .where(OrganizationRoleGrant.org_id.in_(organization_ids))
            ),
        )
    return NativeCreationFacts(*(int(value or 0) for value in counts))


async def cleanup_owned_creation(
    sessions: async_sessionmaker[AsyncSession], owned: NativeOwnedCreation
) -> None:
    async with sessions() as db:
        ids = owned.tenant_ids
        await db.execute(delete(AuditEvent).where(AuditEvent.target_id.in_(tuple(map(str, ids)))))
        await db.execute(delete(TenantInvitation).where(TenantInvitation.tenant_id.in_(ids)))
        await db.execute(
            delete(OrganizationRoleGrant).where(OrganizationRoleGrant.tenant_id.in_(ids))
        )
        await db.execute(delete(OrganizationMember).where(OrganizationMember.tenant_id.in_(ids)))
        await db.execute(delete(TenantRoleGrant).where(TenantRoleGrant.tenant_id.in_(ids)))
        await db.execute(delete(TenantMember).where(TenantMember.tenant_id.in_(ids)))
        await db.execute(delete(Organization).where(Organization.tenant_id.in_(ids)))
        await db.execute(delete(Tenant).where(Tenant.id.in_(ids)))
        await db.execute(delete(User).where(User.id == owned.actor_id))
        await db.commit()
