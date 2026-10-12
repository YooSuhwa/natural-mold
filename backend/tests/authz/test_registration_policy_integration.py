"""Current-schema signup serializes first-admin bootstrap and rolls back failed invite joins."""

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

import anyio
import pytest
from anyio.to_thread import run_sync
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.config import settings
from app.dependencies import get_db
from app.main import create_app
from app.models.organization import OrganizationMember, OrganizationRoleGrant, TenantRoleGrant
from app.models.organization_group import OrgInvitation
from app.models.user import User
from app.services import audit_service
from tests.authz.organization_migration_fixture import async_organization_lab

pytestmark = pytest.mark.integration


@dataclass(frozen=True, slots=True)
class SignupApp:
    client: AsyncClient
    sessions: async_sessionmaker[AsyncSession]


@asynccontextmanager
async def signup_app(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[SignupApp]:
    async with async_organization_lab() as lab:
        await run_sync(command.upgrade, lab.config, "head")
        engine = create_async_engine(lab.engine.url.set(drivername="postgresql+asyncpg"))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            app = create_app()

            async def database_session() -> AsyncGenerator[AsyncSession, None]:
                async with sessions() as db:
                    yield db

            app.dependency_overrides[get_db] = database_session
            monkeypatch.setattr(audit_service, "async_session", sessions)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                yield SignupApp(client, sessions)
        finally:
            await engine.dispose()


async def test_concurrent_signup_bootstraps_exactly_one_platform_org_and_company_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    monkeypatch.setattr(settings, "allow_first_user_as_admin", True)
    async with signup_app(monkeypatch) as stack:
        # Given: an actual migrated empty installation.
        responses: list[Response] = []

        async def signup(email: str) -> None:
            responses.append(
                await stack.client.post(
                    "/api/auth/register",
                    json={
                        "email": email,
                        "password": "correct horse",
                        "name": "Concurrent signup",
                    },
                )
            )

        # When: two account-creation transactions race.
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(signup, "first-native@example.com")
            tasks.start_soon(signup, "second-native@example.com")
        # Then: both join, but exactly one receives each explicit bootstrap authority.
        assert [reply.status_code for reply in responses] == [201, 201]
        assert sorted(reply.json()["user"]["is_super_user"] for reply in responses) == [False, True]
        async with stack.sessions() as db:
            users = list((await db.scalars(select(User))).all())
            org_admins = list(
                (
                    await db.scalars(
                        select(OrganizationRoleGrant).where(OrganizationRoleGrant.role == "admin")
                    )
                ).all()
            )
            company_admins = list((await db.scalars(select(TenantRoleGrant))).all())
            members = list((await db.scalars(select(OrganizationMember))).all())
            assert len(users) == len(members) == 2
            assert len(org_admins) == len(company_admins) == 1
            first = next(user for user in users if user.is_super_user)
            assert org_admins[0].principal_id == company_admins[0].user_id == first.id
            assert all(user.last_active_org_id == org_admins[0].org_id for user in users)


async def test_invitation_backed_signup_rejects_mismatch_without_orphan_and_commits_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    monkeypatch.setattr(settings, "allow_first_user_as_admin", True)
    async with signup_app(monkeypatch) as stack:
        admin = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-inviter@example.com",
                "password": "correct horse",
                "name": "Inviter",
            },
        )
        assert admin.status_code == 201
        async with stack.sessions() as db:
            user = await db.get(User, UUID(admin.json()["user"]["id"]))
            assert user is not None
            org_id = user.last_active_org_id
        invitation = await stack.client.post(
            f"/api/orgs/{org_id}/invitations",
            json={
                "email": "native-invited@example.com",
                "role": "builder",
            },
            headers={"X-CSRF-Token": admin.json()["csrf_token"]},
        )
        assert invitation.status_code == 201
        monkeypatch.setattr(settings, "registration_policy", "invite_only")
        # Given: the real invitation-only installation and an email-bound link.
        token = invitation.json()["token"]
        # When: another email submits the same secret during account creation.
        rejected = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-wrong@example.com",
                "password": "correct horse",
                "name": "Wrong",
                "invitation_token": token,
            },
        )
        # Then: no account is left and the invitation remains pending.
        assert rejected.status_code == 403
        async with stack.sessions() as db:
            assert (
                await db.scalar(select(User.id).where(User.email == "native-wrong@example.com"))
                is None
            )
            row = await db.get(OrgInvitation, UUID(invitation.json()["id"]))
            assert row is not None and row.accepted_at is None
        accepted = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-invited@example.com",
                "password": "correct horse",
                "name": "Invited",
                "invitation_token": token,
            },
        )
        assert accepted.status_code == 201
        user_id = UUID(accepted.json()["user"]["id"])
        async with stack.sessions() as db:
            row = await db.get(OrgInvitation, UUID(invitation.json()["id"]))
            assert row is not None and row.accepted_by == user_id
            assert await db.get(OrganizationMember, (org_id, user_id)) is not None
