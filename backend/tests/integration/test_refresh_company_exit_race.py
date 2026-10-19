"""B-15 on real PostgreSQL: a refresh racing last-company removal cannot leak a session.

Rotation share-locks the user row before the token row; company exit holds the
user row FOR UPDATE before revoking. A rotation that already holds its token lock
therefore makes the exit wait, and the exit then revokes the freshly minted leg.
Runs only in the disposable PostgreSQL lane (``-m integration``).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.jwt import create_refresh_token
from app.config import settings
from app.dependencies import CurrentUser
from app.models.organization import (
    Organization,
    OrganizationMember,
    Tenant,
    TenantMember,
    TenantRoleGrant,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services import auth_service
from app.services.tenants.member_exit import TenantMemberExitRequest, exit_tenant_member


@pytest.fixture
async def maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(settings.database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _request() -> Request:
    return Request(
        {"type": "http", "headers": [(b"user-agent", b"race")], "client": ("127.0.0.1", 1)}
    )


async def _seed(db: AsyncSession) -> tuple[Tenant, CurrentUser, User, str]:
    suffix = uuid.uuid4().hex[:10]
    actor = User(email=f"race-admin-{suffix}@example.com", name="Race admin")
    target = User(email=f"race-member-{suffix}@example.com", name="Race member")
    tenant = Tenant(name="Race", slug=f"race-{suffix}", secrets_namespace=f"race-{suffix}")
    db.add_all([actor, target, tenant])
    await db.flush()
    org = Organization(tenant_id=tenant.id, name="Race org", slug=f"race-org-{suffix}")
    db.add(org)
    await db.flush()
    token, _jti, digest = create_refresh_token(target.id)
    db.add_all(
        [
            TenantMember(tenant_id=tenant.id, user_id=actor.id),
            TenantMember(tenant_id=tenant.id, user_id=target.id),
            TenantRoleGrant(
                tenant_id=tenant.id, user_id=actor.id, role="admin", granted_by=actor.id
            ),
            OrganizationMember(org_id=org.id, tenant_id=tenant.id, user_id=target.id),
            RefreshToken(
                user_id=target.id,
                token_hash=digest,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            ),
        ]
    )
    await db.commit()
    principal = CurrentUser(id=actor.id, email=actor.email, name=actor.name)
    return tenant, principal, target, token


async def test_rotation_holding_token_lock_cannot_outlive_company_exit(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a sole-company member whose refresh rotation pauses after its row locks.
    async with maker() as setup:
        tenant, actor, target, token = await _seed(setup)
    locked, release = asyncio.Event(), asyncio.Event()
    original = auth_service._perform_rotation  # noqa: SLF001

    async def paused(*args: object, **kwargs: object) -> tuple[str, str, str]:
        locked.set()
        await release.wait()
        return await original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(auth_service, "_perform_rotation", paused)

    async def rotate() -> None:
        async with maker() as db:
            await auth_service.rotate_refresh(db, token, _request())
            await db.commit()

    async def remove() -> None:
        async with maker() as db:
            await exit_tenant_member(TenantMemberExitRequest(db, tenant.id, target.id, actor))
            await db.commit()

    # When: the company exit starts while rotation holds its locks, then rotation resumes.
    rotation = asyncio.create_task(rotate())
    await asyncio.wait_for(locked.wait(), 10)
    removal = asyncio.create_task(remove())
    await asyncio.sleep(0.5)
    assert not removal.done()  # exit waits on the user row share lock
    release.set()
    await asyncio.wait_for(asyncio.gather(rotation, removal), 20)

    # Then: no refresh leg survives, including the one minted by the racing rotation.
    async with maker() as db:
        active = await db.scalar(
            select(func.count())
            .select_from(RefreshToken)
            .where(RefreshToken.user_id == target.id, RefreshToken.revoked_at.is_(None))
        )
    assert active == 0
