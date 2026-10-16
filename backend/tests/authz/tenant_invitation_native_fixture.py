"""Canonical PostgreSQL resources for company-invitation race tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.password import hash_password
from app.config import settings
from app.dependencies import CurrentUser, get_db
from app.main import create_app
from app.models.audit_event import AuditEvent
from app.models.organization import Tenant, TenantMember, TenantRoleGrant
from app.models.refresh_token import RefreshToken
from app.models.tenant_invitation import TenantInvitation
from app.models.user import User
from tests.authz.company_administration_migration_fixture import _runner_dsns
from tests.authz.tenant_invitation_fixture import seed_tenant_invitation
from tests.conftest import AuthSession

NATIVE_PASSWORD = "correct horse"


@dataclass(frozen=True, slots=True)
class NativeInvitationCompany:
    tenant_id: UUID
    actor: CurrentUser
    invited_user_id: UUID
    invited_email: str
    owned: NativeOwnedRows


@dataclass(slots=True)
class NativeOwnedRows:
    """Mutable IDs are the cleanup authority for one native company fixture."""

    user_ids: set[UUID] = field(default_factory=set)

    def add_user(self, user_id: UUID) -> None:
        self.user_ids.add(user_id)


@dataclass(frozen=True, slots=True)
class NativeInvitationDatabase:
    primary: async_sessionmaker[AsyncSession]
    competing: async_sessionmaker[AsyncSession]

    @asynccontextmanager
    async def client(
        self, sessions: async_sessionmaker[AsyncSession] | None = None
    ) -> AsyncIterator[AsyncClient]:
        selected = sessions or self.primary

        async def session_override() -> AsyncIterator[AsyncSession]:
            async with selected() as db:
                yield db

        app = create_app()
        app.dependency_overrides[get_db] = session_override
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://native-test"
        ) as client:
            yield client


async def seed_native_company(
    sessions: async_sessionmaker[AsyncSession],
    *,
    invited_email: str,
    existing_invitee: bool = True,
) -> NativeInvitationCompany:
    tenant_id, actor_id, invited_id = uuid4(), uuid4(), uuid4()
    actor = CurrentUser(
        id=actor_id,
        email=f"native-admin-{actor_id.hex[:8]}@example.com",
        name="Native Admin",
        is_super_user=False,
    )
    user_ids = {actor_id, invited_id} if existing_invitee else {actor_id}
    fixture = NativeInvitationCompany(
        tenant_id, actor, invited_id, invited_email, NativeOwnedRows(user_ids)
    )
    try:
        async with sessions() as db:
            db.add(
                Tenant(
                    id=tenant_id,
                    name="Native Invitation Company",
                    slug=f"native-invite-{tenant_id.hex[:12]}",
                    secrets_namespace=f"tenants/{tenant_id}",
                )
            )
            db.add(
                User(
                    id=actor_id,
                    email=actor.email,
                    name=actor.name,
                    hashed_password=hash_password(NATIVE_PASSWORD),
                    is_active=True,
                )
            )
            if existing_invitee:
                db.add(
                    User(
                        id=invited_id,
                        email=invited_email,
                        name="Native Invitee",
                        hashed_password=hash_password(NATIVE_PASSWORD),
                        is_active=True,
                    )
                )
            await db.flush()
            db.add_all(
                [
                    TenantMember(tenant_id=tenant_id, user_id=actor_id),
                    TenantRoleGrant(
                        tenant_id=tenant_id,
                        user_id=actor_id,
                        role="admin",
                        granted_by=actor_id,
                    ),
                ]
            )
            await db.commit()
    except BaseException:
        await cleanup_native_company(sessions, fixture)
        raise
    return fixture


@asynccontextmanager
async def native_company(
    sessions: async_sessionmaker[AsyncSession],
    *,
    invited_email: str,
    existing_invitee: bool = True,
) -> AsyncIterator[NativeInvitationCompany]:
    fixture = await seed_native_company(
        sessions,
        invited_email=invited_email,
        existing_invitee=existing_invitee,
    )
    try:
        yield fixture
    finally:
        await cleanup_native_company(sessions, fixture)


async def login_native(client: AsyncClient, email: str) -> AuthSession:
    response = await client.post(
        "/api/auth/login", json={"email": email, "password": NATIVE_PASSWORD}
    )
    assert response.status_code == 200
    body = response.json()
    cookies = {
        name: response.cookies[name]
        for name in (
            settings.cookie_name_access,
            settings.cookie_name_refresh,
            settings.cookie_name_csrf,
        )
    }
    return AuthSession(body["csrf_token"], cookies, body)


async def seed_native_invitation(
    sessions: async_sessionmaker[AsyncSession], fixture: NativeInvitationCompany
) -> tuple[TenantInvitation, str]:
    async with sessions() as db:
        return await seed_tenant_invitation(
            db,
            tenant_id=fixture.tenant_id,
            created_by=fixture.actor.id,
            email=fixture.invited_email,
        )


async def invitation_audit_count(
    sessions: async_sessionmaker[AsyncSession], tenant_id: UUID, action: str
) -> int:
    async with sessions() as db:
        count = await db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.action == action,
                AuditEvent.event_metadata["tenant_id"].as_string() == str(tenant_id),
            )
        )
        return count or 0


async def cleanup_native_company(
    sessions: async_sessionmaker[AsyncSession], fixture: NativeInvitationCompany
) -> None:
    async with sessions() as db:
        user_ids = tuple(fixture.owned.user_ids)
        await db.execute(
            delete(AuditEvent).where(
                (AuditEvent.actor_user_id.in_(user_ids))
                | (AuditEvent.owner_user_id.in_(user_ids))
                | (AuditEvent.target_owner_user_id.in_(user_ids))
            )
        )
        await db.execute(delete(RefreshToken).where(RefreshToken.user_id.in_(user_ids)))
        await db.execute(delete(Tenant).where(Tenant.id == fixture.tenant_id))
        await db.execute(delete(User).where(User.id.in_(user_ids)))
        await db.commit()


@pytest.fixture
async def tenant_invitation_native_database() -> AsyncIterator[NativeInvitationDatabase]:
    dsn = _runner_dsns().async_url
    primary_engine = create_async_engine(dsn, pool_size=2, max_overflow=0)
    competing_engine = create_async_engine(dsn, pool_size=2, max_overflow=0)
    database = NativeInvitationDatabase(
        async_sessionmaker(primary_engine, expire_on_commit=False),
        async_sessionmaker(competing_engine, expire_on_commit=False),
    )
    try:
        yield database
    finally:
        await primary_engine.dispose()
        await competing_engine.dispose()
