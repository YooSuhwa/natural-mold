"""Operator suspend/resume client fixture (parent Task 13 QA).

Uses the globally registered platform and company overview routers on a private
app instance with the real cookie/JWT/CSRF path and one database session per
request (no auth or CSRF overrides).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token, create_csrf_token
from app.authz.org_context import OrgContext
from app.config import settings
from app.dependencies import get_db
from app.main import create_app
from app.migrations.org_authz_v1.contracts import ORG, TENANT
from app.models.organization import Tenant, TenantRoleGrant
from app.models.user import User
from app.services import audit_service
from app.services.tenants.versioning import etag
from tests import conftest as shared
from tests.authz.registration_fixture import seed_registration_default


@dataclass(frozen=True, slots=True)
class LifecycleCase:
    operator: AsyncClient
    member: AsyncClient
    operator_id: UUID
    operator_csrf: str
    member_id: UUID
    member_csrf: str
    tenant_id: UUID
    org_id: UUID

    @property
    def context(self) -> OrgContext:
        return OrgContext(org_id=self.org_id, tenant_id=self.tenant_id, user_id=self.member_id)

    def member_headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.member_csrf}

    async def version(self) -> str:
        async with shared.TestSession() as db:
            tenant = await db.get(Tenant, self.tenant_id)
            assert tenant is not None
            return etag(tenant.updated_at)

    async def transition(
        self,
        action: str,
        *,
        version: str | None = None,
        csrf: bool = True,
        body: dict[str, object] | None = None,
    ) -> Response:
        headers = {"If-Match": version or await self.version()}
        if csrf:
            headers["X-CSRF-Token"] = self.operator_csrf
        return await self.operator.post(
            f"/api/admin/tenants/{self.tenant_id}/{action}", headers=headers, json=body
        )


def _transport(app: FastAPI) -> ASGITransport:
    return ASGITransport(app=app, raise_app_exceptions=False)


@pytest.fixture
async def lifecycle_case(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[LifecycleCase, None]:
    monkeypatch.setattr(settings, "deployment_mode", "multi_tenant")
    monkeypatch.setattr(audit_service, "async_session", shared.TestSession)
    app = create_app()
    app.dependency_overrides[get_db] = shared.override_get_db
    await seed_registration_default(db)
    operator = User(
        id=uuid4(),
        email=f"task13-operator-{uuid4().hex[:6]}@example.com",
        name="Task 13 Operator",
        hashed_password="unused",
        is_active=True,
        is_super_user=True,
    )
    db.add(operator)
    await db.commit()
    operator_csrf = create_csrf_token(operator.id)
    try:
        async with (
            AsyncClient(transport=_transport(app), base_url="http://test") as op_client,
            AsyncClient(transport=_transport(app), base_url="http://test") as member,
        ):
            op_client.cookies.set(
                settings.cookie_name_access, create_access_token(operator.id, is_super_user=True)
            )
            op_client.cookies.set(settings.cookie_name_csrf, operator_csrf)
            login = await shared.register_session(
                member, email="task13-member@example.com", name="Member"
            )
            member_id = await db.scalar(
                select(User.id).where(User.email == "task13-member@example.com")
            )
            assert member_id is not None
            # An explicit company admin role must not override suspension.
            db.add(TenantRoleGrant(tenant_id=TENANT, user_id=member_id, role="admin"))
            await db.commit()
            yield LifecycleCase(
                operator=op_client,
                member=member,
                operator_id=operator.id,
                operator_csrf=operator_csrf,
                member_id=member_id,
                member_csrf=login.csrf,
                tenant_id=TENANT,
                org_id=ORG,
            )
    finally:
        app.dependency_overrides.clear()
