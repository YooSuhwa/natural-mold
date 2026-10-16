"""Company membership end revokes refresh tokens; suspension keeps them (plan 8.1, 16.6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from fastapi import Request
from sqlalchemy import event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_refresh_token
from app.authz.session_revocation import revoke_sessions_for_deleted_tenant
from app.config import settings
from app.exceptions import AppError
from app.models.organization import Tenant, TenantMember
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services import auth_service
from app.services.tenants.member_exit import TenantMemberExitRequest, exit_tenant_member
from tests.authz.tenant_member_exit_fixture import company_exit_fixture


def _request() -> Request:
    return Request(
        {"type": "http", "headers": [(b"user-agent", b"pytest")], "client": ("127.0.0.1", 1)}
    )


async def _issue_refresh(db: AsyncSession, user_id: UUID) -> str:
    token, _jti, digest = create_refresh_token(user_id)
    db.add(
        RefreshToken(
            user_id=user_id,
            token_hash=digest,
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days),
        )
    )
    await db.commit()
    return token


async def _active_rows(db: AsyncSession, user_id: UUID) -> int:
    rows = await db.scalars(
        select(RefreshToken.id).where(
            RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
        )
    )
    return len(rows.all())


async def _other_company(db: AsyncSession, user_id: UUID, status: str = "active") -> Tenant:
    tenant = Tenant(name="Other company", slug="other-company", secrets_namespace="other")
    db.add(tenant)
    await db.flush()
    db.add(TenantMember(tenant_id=tenant.id, user_id=user_id, status=status))
    await db.commit()
    return tenant


async def test_removal_from_last_company_revokes_refresh_tokens(db: AsyncSession) -> None:
    # Given: a member of exactly one company holding a live refresh token.
    fixture = await company_exit_fixture(db)
    token = await _issue_refresh(db, fixture.target.id)

    # When: the company removes the member and the caller commits.
    await exit_tenant_member(
        TenantMemberExitRequest(db, fixture.tenant.id, fixture.target.id, fixture.actor)
    )
    await db.commit()

    # Then: every refresh row is revoked and rotation fails as an expired session.
    assert await _active_rows(db, fixture.target.id) == 0
    with pytest.raises(AppError) as captured:
        await auth_service.rotate_refresh(db, token, _request())
    assert captured.value.code == "invalid_refresh"


@pytest.mark.parametrize("other_status", ["active", "suspended"])
async def test_removal_keeps_refresh_tokens_while_another_company_remains(
    db: AsyncSession, other_status: str
) -> None:
    # Given: the member still belongs (possibly suspended) to a second company.
    fixture = await company_exit_fixture(db)
    await _other_company(db, fixture.target.id, other_status)
    await _issue_refresh(db, fixture.target.id)

    # When: only the first company removes the member.
    await exit_tenant_member(
        TenantMemberExitRequest(db, fixture.tenant.id, fixture.target.id, fixture.actor)
    )
    await db.commit()

    # Then: the login session survives; other-company authority stays fresh per request.
    assert await _active_rows(db, fixture.target.id) == 1


async def test_failed_company_exit_leaves_refresh_tokens(db: AsyncSession) -> None:
    # Given: the actor is the last company administrator (exit refused).
    fixture = await company_exit_fixture(db)
    await _issue_refresh(db, fixture.actor.id)

    # When: the actor tries to remove themself.
    with pytest.raises(AppError):
        await exit_tenant_member(
            TenantMemberExitRequest(db, fixture.tenant.id, fixture.actor.id, fixture.actor)
        )

    # Then: no session was revoked.
    assert await _active_rows(db, fixture.actor.id) == 1


async def test_deleted_company_hook_revokes_members_without_another_company(
    db: AsyncSession,
) -> None:
    # Given: two sole-company members, a multi-company member, a removed member, an outsider.
    fixture = await company_exit_fixture(db)
    multi = User(email="multi-company@example.com", name="Multi")
    db.add(multi)
    await db.flush()
    db.add(TenantMember(tenant_id=fixture.tenant.id, user_id=multi.id))
    await db.commit()
    await _other_company(db, multi.id)
    await _issue_refresh(db, multi.id)
    removed = User(email="removed-member@example.com", name="Removed")
    outsider = User(email="outsider@example.com", name="Outsider")
    db.add_all([removed, outsider])
    await db.flush()
    db.add(TenantMember(tenant_id=fixture.tenant.id, user_id=removed.id, status="removed"))
    await db.commit()
    for user_id in (fixture.actor.id, fixture.target.id, removed.id, outsider.id):
        await _issue_refresh(db, user_id)

    # When: the purge flow calls the hook before deleting membership rows.
    revoked = await revoke_sessions_for_deleted_tenant(db, fixture.tenant.id)
    await db.commit()

    # Then: only members losing their last company are force-logged-out.
    assert set(revoked) == {fixture.actor.id, fixture.target.id}
    assert await _active_rows(db, fixture.actor.id) == 0
    assert await _active_rows(db, fixture.target.id) == 0
    assert await _active_rows(db, removed.id) == 1
    assert await _active_rows(db, outsider.id) == 1
    assert await _active_rows(db, multi.id) == 1


async def test_rotation_locks_user_row_before_refresh_token_row(db: AsyncSession) -> None:
    # Given: a live refresh token and a recorder of executed SQL.
    fixture = await company_exit_fixture(db)
    token = await _issue_refresh(db, fixture.target.id)
    statements: list[str] = []

    def record(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
        statements.append(statement.lower())

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        # When: the token rotates.
        await auth_service.rotate_refresh(db, token, _request())
    finally:
        event.remove(engine, "before_cursor_execute", record)

    # Then: the user row is read (share-locked on PostgreSQL) before the token row,
    # matching company exit's user -> token order.
    first_user = next(i for i, sql in enumerate(statements) if "from users" in sql)
    first_token = next(i for i, sql in enumerate(statements) if "from refresh_tokens" in sql)
    assert first_user < first_token


def test_rotation_user_lock_is_for_share_on_postgresql(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_service, "is_postgres", lambda _db: True)
    stmt = auth_service._lock_select(select(User.id), cast(AsyncSession, None), read=True)  # noqa: SLF001
    assert "FOR SHARE" in str(stmt.compile(dialect=postgresql.dialect()))
