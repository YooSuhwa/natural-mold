"""Native assembled-app HTTP lifecycle across B12 operator and B11 company administration.

Runs only in the canonical runner lane (``suspension_lane`` binds the runner-owned DSN).
Every request goes through ``create_app()`` with the globally registered routers: no
app-local router mount. Flow: operator creates a company, the invited first admin accepts,
creates an organization and invites a member, archives/restores that organization, and the
operator suspends and resumes the company.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token, create_csrf_token
from app.config import settings
from app.dependencies import get_db
from app.main import create_app
from app.models.audit_event import AuditEvent
from app.models.user import User
from tests.authz.platform_tenant_suspension_native_fixture import SuspensionLane

pytestmark = pytest.mark.integration
pytest_plugins = ["tests.authz.platform_tenant_suspension_native_fixture"]
ACTIONS = (
    "tenant.create",
    "tenant.invitation_create",
    "tenant.invitation_accept",
    "tenant.member_add",
    "tenant.role_grant",
    "org.create",
    "org.archive",
    "org.restore",
    "tenant.suspend",
    "tenant.resume",
)


def _assembled_app(lane: SuspensionLane) -> FastAPI:
    app = create_app()

    async def database_session() -> AsyncGenerator[AsyncSession, None]:
        async with lane.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database_session
    return app


@asynccontextmanager
async def _client(app: FastAPI, user_id: UUID, *, super_user: bool) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://task14") as client:
        csrf = create_csrf_token(user_id)
        client.cookies.set(
            settings.cookie_name_access, create_access_token(user_id, is_super_user=super_user)
        )
        client.cookies.set(settings.cookie_name_csrf, csrf)
        client.headers["X-CSRF-Token"] = csrf
        yield client


async def _user(lane: SuspensionLane, email: str, *, super_user: bool = False) -> UUID:
    async with lane.sessions() as db:
        user = User(email=email, name=email, is_active=True, is_super_user=super_user)
        db.add(user)
        await db.commit()
        return user.id


async def _audit_counts(lane: SuspensionLane, tenant_id: UUID) -> Counter[str]:
    async with lane.sessions() as db:
        rows = (await db.scalars(select(AuditEvent).where(AuditEvent.action.in_(ACTIONS)))).all()
    return Counter(
        row.action
        for row in rows
        if row.event_metadata is not None and row.event_metadata.get("tenant_id") == str(tenant_id)
    )


def _ok(response: Response, status: int) -> Response:
    assert response.status_code == status, response.text
    return response


async def test_native_company_lifecycle_through_assembled_routes(
    suspension_lane: SuspensionLane,
) -> None:
    key = uuid4().hex[:10]
    admin_email, member_email = f"t14-admin-{key}@example.com", f"t14-member-{key}@example.com"
    operator_id = await _user(suspension_lane, f"t14-op-{key}@example.com", super_user=True)
    admin_id = await _user(suspension_lane, admin_email)
    member_id = await _user(suspension_lane, member_email)
    app = _assembled_app(suspension_lane)
    try:
        async with (
            _client(app, operator_id, super_user=True) as operator,
            _client(app, admin_id, super_user=False) as admin,
            _client(app, member_id, super_user=False) as member,
        ):
            created = _ok(
                await operator.post(
                    "/api/admin/tenants",
                    json={
                        "name": "Task 14 company",
                        "slug": f"t14-{key}",
                        "first_admin_email": admin_email,
                        "first_organization_name": "Task 14 default",
                        "member_limit": 5,
                        "monthly_spend_limit": "100.00",
                        "concurrent_run_limit": 2,
                        "allow_system_models": False,
                    },
                ),
                201,
            ).json()
            tenant_id = UUID(created["tenant"]["id"])
            assert created["organization"]["slug"] == "default"
            accepted = _ok(
                await admin.post(
                    "/api/invitations/accept", json={"token": created["invitation"]["token"]}
                ),
                200,
            )
            assert accepted.json() == {
                "org_id": None,
                "tenant_id": str(tenant_id),
                "user_id": str(admin_id),
            }

            organization = _ok(
                await admin.post(
                    f"/api/tenants/{tenant_id}/organizations",
                    json={"name": "Task 14 team", "default_member_role": "member"},
                ),
                201,
            )
            org_id = organization.json()["organization"]["id"]
            overview = _ok(await admin.get(f"/api/tenants/{tenant_id}"), 200)
            invited = _ok(
                await admin.post(
                    f"/api/tenants/{tenant_id}/invitations",
                    json={"email": member_email, "roles": []},
                    headers={"If-Match": overview.headers["etag"]},
                ),
                201,
            )
            _ok(
                await member.post(
                    "/api/invitations/accept", json={"token": invited.json()["token"]}
                ),
                200,
            )

            org_path = f"/api/tenants/{tenant_id}/organizations/{org_id}"
            archived = _ok(
                await admin.post(
                    f"{org_path}/archive",
                    json={"confirm_name": "Task 14 team"},
                    headers={"If-Match": organization.headers["etag"]},
                ),
                200,
            )
            assert archived.json()["status"] == "archived"
            assert archived.headers["etag"] != organization.headers["etag"]
            restored = _ok(
                await admin.post(
                    f"{org_path}/restore", headers={"If-Match": archived.headers["etag"]}
                ),
                200,
            )
            assert restored.json()["status"] == "active"

            detail = _ok(await operator.get(f"/api/admin/tenants/{tenant_id}"), 200)
            suspended = _ok(
                await operator.post(
                    f"/api/admin/tenants/{tenant_id}/suspend",
                    headers={"If-Match": detail.headers["etag"]},
                ),
                200,
            )
            assert suspended.json()["status"] == "suspended"
            denied = await admin.post(
                f"/api/tenants/{tenant_id}/invitations",
                json={"email": f"t14-late-{key}@example.com", "roles": []},
                headers={"If-Match": suspended.headers["etag"]},
            )
            assert denied.status_code == 403
            assert denied.json()["error"]["code"] == "tenant_suspended"
            resumed = _ok(
                await operator.post(
                    f"/api/admin/tenants/{tenant_id}/resume",
                    headers={"If-Match": suspended.headers["etag"]},
                ),
                200,
            )
            assert resumed.json()["status"] == "active"
            assert resumed.headers["etag"] != suspended.headers["etag"]
            _ok(await admin.get(f"/api/tenants/{tenant_id}"), 200)
    finally:
        app.dependency_overrides.clear()

    assert await _audit_counts(suspension_lane, tenant_id) == Counter(
        {
            "tenant.create": 1,
            "tenant.invitation_create": 1,
            "tenant.invitation_accept": 2,
            "tenant.member_add": 2,
            "tenant.role_grant": 1,
            "org.create": 1,
            "org.archive": 1,
            "org.restore": 1,
            "tenant.suspend": 1,
            "tenant.resume": 1,
        }
    )
