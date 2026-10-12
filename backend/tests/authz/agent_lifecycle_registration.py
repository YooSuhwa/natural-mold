"""Real registration, JWT, CSRF, and role setup for B08 native tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.dependencies import get_db
from app.main import create_app
from app.models.organization import (
    OrganizationMember,
    OrganizationRoleGrant,
    TenantMember,
    TenantRoleGrant,
)
from app.models.organization_group import Group, GroupMember
from app.models.user import User
from app.services import audit_service
from tests.authz.agent_lifecycle_fixture import LifecycleActors, LifecycleSentinel


@dataclass(frozen=True, slots=True)
class RegisteredActors:
    sessions: async_sessionmaker[AsyncSession]
    actors: LifecycleActors


async def _register_actor(
    client: AsyncClient,
    email: str,
    sentinel: LifecycleSentinel,
) -> UUID:
    response = await client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "fixture password 42",
            "display_name": email.split("@", 1)[0],
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    csrf = payload["csrf_token"]
    actor_id = UUID(payload["user"]["id"])
    assert client.cookies.get("moldy_at")
    authenticated = await client.get("/api/auth/me")
    assert authenticated.status_code == 200
    csrf_checked = await client.patch(
        "/api/auth/me/profile",
        json={"avatar_color": "slate"},
        headers={"X-CSRF-Token": csrf},
    )
    assert csrf_checked.status_code == 200
    sentinel.observe("registered")
    return actor_id


async def _assign_roles(
    sessions: async_sessionmaker[AsyncSession],
    user_ids: tuple[UUID, ...],
    invocation: str,
) -> LifecycleActors:
    async with sessions() as db:
        users = list((await db.scalars(select(User).where(User.id.in_(user_ids)))).all())
        by_id = {user.id: user for user in users}
        owner_one, owner_two, direct, grouped, company, operator, revoked, inactive = user_ids
        first = by_id[owner_one]
        assert first.last_active_org_id is not None
        org_id = first.last_active_org_id
        membership = await db.get(OrganizationMember, (org_id, owner_one))
        assert membership is not None
        tenant_id = membership.tenant_id
        await db.execute(delete(OrganizationRoleGrant))
        await db.execute(delete(TenantRoleGrant))
        await db.execute(update(User).values(is_super_user=False, is_active=True))
        group_id = uuid4()
        db.add(
            Group(
                id=group_id,
                tenant_id=tenant_id,
                org_id=org_id,
                name=f"Lifecycle administrators {invocation}",
                description="B08 fixture",
                source="manual",
                external_id=None,
                created_by=owner_one,
            )
        )
        db.add(
            GroupMember(
                group_id=group_id,
                user_id=grouped,
                org_id=org_id,
                tenant_id=tenant_id,
                added_by=owner_one,
            )
        )
        for principal_type, principal_id in (
            ("user", direct),
            ("group", group_id),
            ("user", revoked),
            ("user", inactive),
        ):
            db.add(
                OrganizationRoleGrant(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    org_id=org_id,
                    role="admin",
                    principal_type=principal_type,
                    principal_id=principal_id,
                    granted_by=owner_one,
                )
            )
        db.add(
            TenantRoleGrant(
                id=uuid4(),
                tenant_id=tenant_id,
                user_id=company,
                role="admin",
                granted_by=owner_one,
            )
        )
        by_id[operator].is_super_user = True
        by_id[inactive].is_active = False
        revoked_org = await db.get(OrganizationMember, (org_id, revoked))
        revoked_tenant = await db.get(TenantMember, (tenant_id, revoked))
        assert revoked_org is not None and revoked_tenant is not None
        revoked_org.status = "removed"
        revoked_tenant.status = "removed"
        await db.commit()
    return LifecycleActors(
        owner_one,
        owner_two,
        direct,
        grouped,
        company,
        operator,
        revoked,
        inactive,
        tenant_id,
        org_id,
    )


@asynccontextmanager
async def registered_lifecycle_actors(
    monkeypatch: pytest.MonkeyPatch,
    sentinel: LifecycleSentinel,
) -> AsyncIterator[RegisteredActors]:
    """Register all actors through real cookies and verify one CSRF mutation each."""
    source = make_url(settings.database_url)
    engine = create_async_engine(source)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    monkeypatch.setattr(settings, "allow_first_user_as_admin", True)
    monkeypatch.setattr(audit_service, "async_session", sessions)
    app = create_app()

    async def database_session() -> AsyncGenerator[AsyncSession, None]:
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database_session
    user_ids: list[UUID] = []
    invocation = uuid4().hex
    try:
        for label in (
            "owner-one",
            "owner-two",
            "direct-admin",
            "group-admin",
            "company-admin",
            "operator",
            "revoked-admin",
            "inactive-admin",
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://fixture",
            ) as client:
                user_ids.append(
                    await _register_actor(
                        client,
                        f"{label}-b08-{invocation}@example.com",
                        sentinel,
                    )
                )
        actors = await _assign_roles(sessions, tuple(user_ids), invocation)
        yield RegisteredActors(sessions, actors)
    finally:
        await engine.dispose()


__all__ = ["RegisteredActors", "registered_lifecycle_actors"]
