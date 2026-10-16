"""Real-auth HTTP fixture for company member and role administration."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.password import hash_password
from app.dependencies import get_db
from app.main import create_app
from app.models.organization import Tenant, TenantMember, TenantRoleGrant
from app.models.user import User
from app.services.tenants.versioning import etag

PASSWORD = "correct horse"


@dataclass(frozen=True, slots=True)
class MemberSession:
    csrf: str

    def headers(self, version: str) -> dict[str, str]:
        return {"X-CSRF-Token": self.csrf, "If-Match": version}


@dataclass(frozen=True, slots=True)
class TenantMembersFixture:
    db: AsyncSession
    client: AsyncClient
    tenant: Tenant
    admin: User
    peer_admin: User
    member: User
    target: User
    inactive: User

    async def login(self, user: User) -> MemberSession:
        self.client.cookies.clear()
        response = await self.client.post(
            "/api/auth/login", json={"email": user.email, "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        return MemberSession(response.json()["csrf_token"])

    async def version(self) -> str:
        await self.db.refresh(self.tenant)
        return etag(self.tenant.updated_at)


def _app(db: AsyncSession) -> FastAPI:
    app = create_app()

    async def session() -> AsyncGenerator[AsyncSession, None]:
        yield db

    app.dependency_overrides[get_db] = session
    return app


@pytest.fixture
async def tenant_members_fixture(db: AsyncSession) -> AsyncGenerator[TenantMembersFixture, None]:
    users = (
        User(
            email="member-admin@example.com",
            name="Member Admin",
            hashed_password=hash_password(PASSWORD),
            is_active=True,
        ),
        User(
            email="member-peer-admin@example.com",
            name="Peer Admin",
            hashed_password=hash_password(PASSWORD),
            is_active=True,
        ),
        User(
            email="member-plain@example.com",
            name="Plain Member",
            hashed_password=hash_password(PASSWORD),
            is_active=True,
        ),
        User(
            email="member-target@example.com",
            name="Target Account",
            hashed_password=hash_password(PASSWORD),
            is_active=True,
        ),
        User(
            email="member-inactive@example.com",
            name="Inactive Account",
            hashed_password=hash_password(PASSWORD),
            is_active=False,
        ),
    )
    tenant = Tenant(
        name="Member Company", slug="member-company", secrets_namespace="member-company"
    )
    db.add_all((*users, tenant))
    await db.flush()
    admin, peer_admin, member, target, inactive = users
    created = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=3)
    db.add_all(
        (
            TenantMember(
                tenant_id=tenant.id, user_id=admin.id, status="active", created_at=created
            ),
            TenantMember(
                tenant_id=tenant.id,
                user_id=peer_admin.id,
                status="active",
                created_at=created + timedelta(minutes=1),
            ),
            TenantMember(
                tenant_id=tenant.id,
                user_id=member.id,
                status="active",
                created_at=created + timedelta(minutes=2),
            ),
            TenantRoleGrant(
                tenant_id=tenant.id,
                user_id=admin.id,
                role="admin",
                granted_by=admin.id,
            ),
            TenantRoleGrant(
                tenant_id=tenant.id,
                user_id=peer_admin.id,
                role="admin",
                granted_by=admin.id,
            ),
        )
    )
    await db.commit()
    await db.refresh(tenant)
    async with AsyncClient(transport=ASGITransport(app=_app(db)), base_url="http://test") as client:
        yield TenantMembersFixture(db, client, tenant, admin, peer_admin, member, target, inactive)
