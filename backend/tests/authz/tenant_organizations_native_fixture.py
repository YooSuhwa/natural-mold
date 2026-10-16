"""PostgreSQL-only fixtures for organization creation and administrator changes."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.dependencies import get_db
from app.main import create_app
from app.migrations.org_authz_v1.contracts import ORG, TENANT
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
from tests.authz.company_administration_migration_fixture import _runner_dsns
from tests.authz.registration_fixture import clear_default_membership, seed_registration_default
from tests.conftest import AuthSession, register_session


@dataclass(frozen=True, slots=True)
class NativeTask7Sessions:
    http: async_sessionmaker[AsyncSession]
    observer: async_sessionmaker[AsyncSession]
    owned: NativeTask7OwnedRows


@dataclass(slots=True)
class NativeTask7OwnedRows:
    tenant_ids: set[UUID] = field(default_factory=set)
    user_ids: set[UUID] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class NativeTask7Case:
    auth: AuthSession
    actor_id: UUID
    target_id: UUID
    replacement_id: UUID
    unrelated_id: UUID
    tenant_id: UUID
    org_id: UUID


def _engine(name: str) -> AsyncEngine:
    return create_async_engine(
        _runner_dsns().async_url,
        connect_args={"server_settings": {"application_name": name}},
    )


@pytest.fixture
async def native_task7_sessions() -> AsyncIterator[NativeTask7Sessions]:
    engines = (_engine("task7_http"), _engine("task7_observer"))
    factories = tuple(async_sessionmaker(engine, expire_on_commit=False) for engine in engines)
    created_tenant = False
    created_org = False
    try:
        async with factories[1]() as db:
            created_tenant = await db.get(Tenant, TENANT) is None
            created_org = await db.get(Organization, ORG) is None
            await seed_registration_default(db)
        sessions = NativeTask7Sessions(factories[0], factories[1], NativeTask7OwnedRows())
        yield sessions
    finally:
        async with factories[1]() as db:
            if created_org:
                await db.execute(delete(Organization).where(Organization.id == ORG))
            if created_tenant:
                await db.execute(delete(Tenant).where(Tenant.id == TENANT))
            await db.commit()
        for engine in engines:
            await engine.dispose()


@asynccontextmanager
async def native_task7_client(
    sessions: NativeTask7Sessions,
) -> AsyncGenerator[AsyncClient, None]:
    app = create_app()

    async def database_session() -> AsyncGenerator[AsyncSession, None]:
        async with sessions.http() as db:
            yield db

    app.dependency_overrides[get_db] = database_session
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


async def seed_native_task7(sessions: NativeTask7Sessions, client: AsyncClient) -> NativeTask7Case:
    key = uuid4().hex
    auth = await register_session(
        client, email=f"task7-native-admin-{key}@example.com", name="Task7 native admin"
    )
    actor_id = UUID(auth.body["user"]["id"])
    sessions.owned.user_ids.add(actor_id)
    async with sessions.observer() as db:
        await clear_default_membership(db, actor_id)
        target = User(
            id=uuid4(), email=f"task7-native-target-{key}@example.com", name="Task7 target"
        )
        replacement = User(
            id=uuid4(),
            email=f"task7-native-replacement-{key}@example.com",
            name="Task7 replacement",
        )
        unrelated = User(
            id=uuid4(),
            email=f"task7-native-unrelated-{key}@example.com",
            name="Task7 unrelated",
        )
        tenant = Tenant(
            id=uuid4(),
            name="Task7 native company",
            slug=f"task7-native-{key}",
            secrets_namespace=f"task7/native/{key}",
        )
        sessions.owned.user_ids.update((target.id, replacement.id, unrelated.id))
        sessions.owned.tenant_ids.add(tenant.id)
        db.add_all((target, replacement, unrelated, tenant))
        await db.flush()
        org = Organization(
            tenant_id=tenant.id,
            name="Initial",
            slug=f"initial-{key}",
            settings={"default_member_role": "member", "preserved": True},
        )
        db.add(org)
        await db.flush()
        db.add_all(
            (
                TenantMember(tenant_id=tenant.id, user_id=actor_id),
                TenantMember(tenant_id=tenant.id, user_id=target.id),
                TenantMember(tenant_id=tenant.id, user_id=replacement.id),
                TenantMember(tenant_id=tenant.id, user_id=unrelated.id),
                TenantRoleGrant(
                    tenant_id=tenant.id, user_id=actor_id, role="admin", granted_by=actor_id
                ),
                OrganizationMember(org_id=org.id, tenant_id=tenant.id, user_id=target.id),
                OrganizationMember(org_id=org.id, tenant_id=tenant.id, user_id=replacement.id),
                OrganizationMember(org_id=org.id, tenant_id=tenant.id, user_id=unrelated.id),
                OrganizationRoleGrant(
                    org_id=org.id,
                    tenant_id=tenant.id,
                    principal_type="user",
                    principal_id=target.id,
                    role="admin",
                    granted_by=actor_id,
                ),
                OrganizationRoleGrant(
                    org_id=org.id,
                    tenant_id=tenant.id,
                    principal_type="user",
                    principal_id=unrelated.id,
                    role="auditor",
                    granted_by=actor_id,
                ),
            )
        )
        await db.commit()
        return NativeTask7Case(
            auth, actor_id, target.id, replacement.id, unrelated.id, tenant.id, org.id
        )


@pytest.fixture
async def native_task7_cleanup_guard(
    native_task7_sessions: NativeTask7Sessions,
) -> AsyncIterator[None]:
    yield
    async with native_task7_sessions.observer() as db:
        tenant_ids = tuple(native_task7_sessions.owned.tenant_ids)
        user_ids = tuple(native_task7_sessions.owned.user_ids)
        if user_ids:
            for user_column in (
                AuditEvent.actor_user_id,
                AuditEvent.owner_user_id,
                AuditEvent.target_owner_user_id,
            ):
                await db.execute(delete(AuditEvent).where(user_column.in_(user_ids)))
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
            await db.execute(
                delete(OrganizationRoleGrant).where(
                    OrganizationRoleGrant.principal_type == "user",
                    OrganizationRoleGrant.principal_id.in_(user_ids),
                )
            )
            await db.execute(
                delete(OrganizationMember).where(OrganizationMember.user_id.in_(user_ids))
            )
            await db.execute(delete(TenantRoleGrant).where(TenantRoleGrant.user_id.in_(user_ids)))
            await db.execute(delete(TenantMember).where(TenantMember.user_id.in_(user_ids)))
            await db.execute(delete(User).where(User.id.in_(user_ids)))
        await db.commit()
