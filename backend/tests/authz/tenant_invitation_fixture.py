"""Shared company-invitation setup for ordinary API tests."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_tenant_invitation_token
from app.dependencies import get_db
from app.main import create_app
from app.models.organization import TenantRoleGrant
from app.models.tenant_invitation import TenantInvitation
from app.models.user import User
from tests.conftest import AuthSession, override_get_db, register_session


@dataclass(frozen=True, slots=True)
class TenantInvitationFixture:
    id: UUID
    token: str
    recipient_id: UUID
    session: AuthSession


@pytest.fixture
async def tenant_invitation_client() -> AsyncGenerator[AsyncClient, None]:
    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def grant_tenant_admin(db: AsyncSession, tenant_id: UUID, user_id: UUID) -> None:
    db.add(
        TenantRoleGrant(
            tenant_id=tenant_id,
            user_id=user_id,
            role="admin",
            granted_by=user_id,
        )
    )
    await db.commit()


async def seed_tenant_invitation(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    created_by: UUID | None,
    email: str,
    role: str | None = "admin",
) -> tuple[TenantInvitation, str]:
    invitation_id = uuid4()
    token = create_tenant_invitation_token(invitation_id)
    now = datetime.now(UTC)
    row = TenantInvitation(
        id=invitation_id,
        tenant_id=tenant_id,
        email=email,
        role=role,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        created_by=created_by,
        created_at=now,
        expires_at=now + timedelta(days=7),
    )
    db.add(row)
    await db.commit()
    return row, token


async def pending_tenant_invitation(
    db: AsyncSession,
    client: AsyncClient,
    *,
    tenant_id: UUID,
    created_by: UUID,
    email: str = "tenant-invitee@example.com",
    role: str | None = "admin",
) -> TenantInvitationFixture:
    session = await register_session(client, email=email, name="Tenant Invitee")
    recipient = await db.scalar(select(User).where(User.email == email))
    assert recipient is not None
    row, token = await seed_tenant_invitation(
        db,
        tenant_id=tenant_id,
        created_by=created_by,
        email=email,
        role=role,
    )
    return TenantInvitationFixture(row.id, token, recipient.id, session)
