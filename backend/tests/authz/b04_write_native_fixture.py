"""Owned PostgreSQL fixtures for genuine B04 HTTP/company-exit races."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

import anyio
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.dependencies import CurrentUser, get_db
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
from app.models.user import User
from tests.authz.company_administration_migration_fixture import _runner_dsns
from tests.conftest import AuthSession, register_session


@dataclass(frozen=True, slots=True)
class NativeB04Sessions:
    http: async_sessionmaker[AsyncSession]
    exit: async_sessionmaker[AsyncSession]
    observer: async_sessionmaker[AsyncSession]


@dataclass(frozen=True, slots=True)
class NativeB04Case:
    actor: CurrentUser
    auth: AuthSession
    tenant_id: UUID
    target_id: UUID
    initial_org_id: UUID
    add_org_id: UUID


def named_engine(application_name: str) -> AsyncEngine:
    return create_async_engine(
        _runner_dsns().async_url,
        connect_args={"server_settings": {"application_name": application_name}},
    )


@pytest.fixture
async def native_b04_sessions() -> AsyncIterator[NativeB04Sessions]:
    engines = (
        named_engine("b04_http"),
        named_engine("b04_exit"),
        named_engine("b04_observer"),
    )
    try:
        yield NativeB04Sessions(
            *(async_sessionmaker(engine, expire_on_commit=False) for engine in engines)
        )
    finally:
        for engine in engines:
            await engine.dispose()


@asynccontextmanager
async def native_b04_client(
    sessions: NativeB04Sessions,
) -> AsyncGenerator[AsyncClient, None]:
    app = create_app()

    async def database_session() -> AsyncGenerator[AsyncSession, None]:
        async with sessions.http() as db:
            yield db

    app.dependency_overrides[get_db] = database_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


async def seed_native_b04(sessions: NativeB04Sessions, client: AsyncClient) -> NativeB04Case:
    key = uuid4().hex
    auth = await register_session(
        client,
        email=f"b04-native-admin-{key}@example.com",
        name="B04 native admin",
    )
    actor_id = UUID(auth.body["user"]["id"])
    async with sessions.observer() as db:
        target = User(email=f"b04-native-target-{key}@example.test", name="B04 target")
        tenant = Tenant(
            name="B04 native company",
            slug=f"b04-native-{key}",
            secrets_namespace=f"b04/native/{key}",
        )
        db.add_all((target, tenant))
        await db.flush()
        organizations = (
            Organization(tenant_id=tenant.id, name="Initial", slug=f"initial-{key}"),
            Organization(tenant_id=tenant.id, name="Add target", slug=f"add-{key}"),
        )
        db.add_all(organizations)
        await db.flush()
        db.add_all(
            (
                TenantMember(tenant_id=tenant.id, user_id=actor_id),
                TenantMember(tenant_id=tenant.id, user_id=target.id),
                TenantRoleGrant(
                    tenant_id=tenant.id,
                    user_id=actor_id,
                    role="admin",
                    granted_by=actor_id,
                ),
                OrganizationMember(
                    org_id=organizations[0].id,
                    tenant_id=tenant.id,
                    user_id=target.id,
                ),
                OrganizationRoleGrant(
                    org_id=organizations[0].id,
                    tenant_id=tenant.id,
                    principal_type="user",
                    principal_id=actor_id,
                    role="admin",
                    granted_by=actor_id,
                ),
            )
        )
        await db.commit()
        actor = await db.get(User, actor_id)
        assert actor is not None
        return NativeB04Case(
            CurrentUser(id=actor.id, email=actor.email, name=actor.name),
            auth,
            tenant.id,
            target.id,
            organizations[0].id,
            organizations[1].id,
        )


async def wait_for_lock_wait(sessions: NativeB04Sessions, application_name: str) -> None:
    query = text(
        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
        "WHERE application_name = :name AND wait_event_type = 'Lock')"
    )
    with anyio.fail_after(5):
        while True:
            async with sessions.observer() as db:
                if await db.scalar(query, {"name": application_name}):
                    return
            await anyio.sleep(0.01)


async def cleanup_native_b04(sessions: NativeB04Sessions, case: NativeB04Case) -> None:
    async with sessions.observer() as db:
        await db.execute(delete(AuditEvent).where(AuditEvent.target_id == str(case.target_id)))
        await db.execute(
            delete(OrganizationRoleGrant).where(OrganizationRoleGrant.tenant_id == case.tenant_id)
        )
        await db.execute(delete(Organization).where(Organization.tenant_id == case.tenant_id))
        await db.execute(delete(Tenant).where(Tenant.id == case.tenant_id))
        await db.execute(delete(User).where(User.id == case.target_id))
        await db.commit()


@pytest.fixture
async def native_b04_cleanup_guard(
    native_b04_sessions: NativeB04Sessions,
) -> AsyncIterator[None]:
    """Remove every prefixed row even when a race assertion fails early."""
    yield
    async with native_b04_sessions.observer() as db:
        tenant_ids = tuple(
            await db.scalars(select(Tenant.id).where(Tenant.slug.like("b04-native-%")))
        )
        await db.execute(
            delete(AuditEvent).where(AuditEvent.actor_email_snapshot.like("b04-native-admin-%"))
        )
        if tenant_ids:
            await db.execute(
                delete(OrganizationRoleGrant).where(OrganizationRoleGrant.tenant_id.in_(tenant_ids))
            )
            await db.execute(delete(Organization).where(Organization.tenant_id.in_(tenant_ids)))
            await db.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
        await db.execute(
            delete(User).where(
                User.email.like("b04-native-admin-%") | User.email.like("b04-native-target-%")
            )
        )
        await db.commit()
