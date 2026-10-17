"""Isolated HTTP contract for company overview and settings."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import replace
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_csrf_token
from app.config import settings
from app.dependencies import CurrentUser, get_current_user, get_db
from app.exception_handlers import register_exception_handlers
from app.models.audit_event import AuditEvent
from app.models.organization import Organization, Tenant, TenantRoleGrant
from app.routers.tenant_overview import router
from app.services.tenants.versioning import etag

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


def _overview_app(db: AsyncSession, actor: CurrentUser) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)

    async def session() -> AsyncGenerator[AsyncSession, None]:
        yield db

    async def current_user() -> CurrentUser:
        return actor

    app.dependency_overrides[get_db] = session
    app.dependency_overrides[get_current_user] = current_user
    return app


def _csrf_headers(actor: CurrentUser, *, if_match: str | None = None) -> tuple[str, dict[str, str]]:
    token = create_csrf_token(actor.id)
    headers = {"X-CSRF-Token": token}
    if if_match is not None:
        headers["If-Match"] = if_match
    return token, headers


@pytest.mark.parametrize(
    ("role", "expected_keys"),
    [
        (None, {"id", "name", "slug", "status", "updated_at", "access"}),
        (
            "auditor",
            {
                "id",
                "name",
                "slug",
                "status",
                "updated_at",
                "access",
                "active_member_count",
                "organization_count",
                "organizations",
            },
        ),
        (
            "admin",
            {
                "id",
                "name",
                "slug",
                "status",
                "updated_at",
                "access",
                "active_member_count",
                "organization_count",
                "organizations",
                "active_admin_count",
            },
        ),
    ],
)
async def test_get_returns_role_specific_redacted_overview_and_tenant_etag(
    db: AsyncSession,
    org_fixture: OrgFixture,
    role: str | None,
    expected_keys: set[str],
) -> None:
    hidden = Organization(tenant_id=org_fixture.tenant, name="Governed only", slug="governed-only")
    db.add(hidden)
    tenant = await db.get(Tenant, org_fixture.tenant)
    assert tenant is not None
    tenant.slug = "company-a"
    if role is not None:
        db.add(
            TenantRoleGrant(
                tenant_id=org_fixture.tenant,
                user_id=org_fixture.user.id,
                role=role,
            )
        )
    await db.commit()
    app = _overview_app(db, org_fixture.user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/tenants/{org_fixture.tenant}")

    assert response.status_code == 200
    assert set(response.json()) == expected_keys
    assert response.json()["access"] == (role or "member")
    assert response.headers["etag"] == etag(tenant.updated_at)
    rendered = response.text
    assert "secrets_namespace" not in rendered
    assert "data_keys" not in rendered
    assert "settings" not in rendered
    assert ("Governed only" in rendered) == (role in {"auditor", "admin"})


async def test_admin_patch_uses_fresh_etag_preserves_reserved_settings_and_audits(
    db: AsyncSession, org_fixture: OrgFixture
) -> None:
    db.add(TenantRoleGrant(tenant_id=org_fixture.tenant, user_id=org_fixture.user.id, role="admin"))
    tenant = await db.get(Tenant, org_fixture.tenant)
    assert tenant is not None
    tenant.slug = "company-a"
    tenant.settings = {"reserved": "keep", "unknown": {"managed": True}}
    await db.commit()
    await db.refresh(tenant)
    original_etag = etag(tenant.updated_at)
    token, headers = _csrf_headers(org_fixture.user, if_match=original_etag)
    app = _overview_app(db, org_fixture.user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set(settings.cookie_name_csrf, token)
        response = await client.patch(
            f"/api/tenants/{org_fixture.tenant}",
            json={"name": "Renamed Company"},
            headers=headers,
        )

    assert response.status_code == 200
    assert response.json()["name"] == "Renamed Company"
    assert response.headers["etag"] != original_etag
    await db.refresh(tenant)
    assert tenant.settings == {"reserved": "keep", "unknown": {"managed": True}}
    audit = await db.scalar(select(AuditEvent).where(AuditEvent.action == "tenant.update"))
    assert audit is not None
    assert audit.event_metadata == {
        "tenant_id": str(org_fixture.tenant),
        "changed_fields": ["name"],
    }


@pytest.mark.parametrize("if_match", [None, "not-an-etag", '"2000-01-01T00:00:00+00:00"'])
async def test_patch_rejects_missing_malformed_and_stale_if_match(
    db: AsyncSession, org_fixture: OrgFixture, if_match: str | None
) -> None:
    db.add(TenantRoleGrant(tenant_id=org_fixture.tenant, user_id=org_fixture.user.id, role="admin"))
    await db.commit()
    token, headers = _csrf_headers(org_fixture.user, if_match=if_match)
    app = _overview_app(db, org_fixture.user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set(settings.cookie_name_csrf, token)
        response = await client.patch(
            f"/api/tenants/{org_fixture.tenant}", json={"name": "Rejected"}, headers=headers
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "stale_version"
    tenant = await db.get(Tenant, org_fixture.tenant, populate_existing=True)
    assert tenant is not None and tenant.name == "A"
    assert (
        await db.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "tenant.update")
        )
        == 0
    )


@pytest.mark.parametrize("role", [None, "auditor"])
async def test_member_and_auditor_cannot_patch_tenant(
    db: AsyncSession, org_fixture: OrgFixture, role: str | None
) -> None:
    if role is not None:
        db.add(
            TenantRoleGrant(tenant_id=org_fixture.tenant, user_id=org_fixture.user.id, role=role)
        )
    await db.commit()
    tenant = await db.get(Tenant, org_fixture.tenant)
    assert tenant is not None
    token, headers = _csrf_headers(org_fixture.user, if_match=etag(tenant.updated_at))
    app = _overview_app(db, org_fixture.user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set(settings.cookie_name_csrf, token)
        response = await client.patch(
            f"/api/tenants/{org_fixture.tenant}", json={"name": "Rejected"}, headers=headers
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_required"


async def test_operator_flag_has_no_foreign_company_authority(
    db: AsyncSession, org_fixture: OrgFixture
) -> None:
    operator = replace(org_fixture.user, is_super_user=True)
    foreign_tenant = await db.scalar(
        select(Organization.tenant_id).where(Organization.id == org_fixture.foreign)
    )
    assert foreign_tenant is not None
    tenant = await db.get(Tenant, org_fixture.tenant)
    assert tenant is not None
    tenant.slug = "company-a"
    await db.commit()
    app = _overview_app(db, operator)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        own = await client.get(f"/api/tenants/{org_fixture.tenant}")
        foreign = await client.get(f"/api/tenants/{foreign_tenant}")
        missing = await client.get(f"/api/tenants/{uuid4()}")

    assert own.status_code == 200 and own.json()["access"] == "member"
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()


async def test_csrf_failure_precedes_foreign_company_lookup(
    db: AsyncSession, org_fixture: OrgFixture
) -> None:
    app = _overview_app(db, org_fixture.user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(f"/api/tenants/{uuid4()}", json={"name": "Forbidden"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_mismatch"


async def test_patch_accepts_the_json_body_version_as_if_match(
    db: AsyncSession, org_fixture: OrgFixture
) -> None:
    db.add(TenantRoleGrant(tenant_id=org_fixture.tenant, user_id=org_fixture.user.id, role="admin"))
    tenant = await db.get(Tenant, org_fixture.tenant)
    assert tenant is not None
    tenant.slug = "company-a"
    await db.commit()
    app = _overview_app(db, org_fixture.user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        body_version = (await client.get(f"/api/tenants/{org_fixture.tenant}")).json()["updated_at"]
        token, headers = _csrf_headers(org_fixture.user, if_match=f'"{body_version}"')
        client.cookies.set(settings.cookie_name_csrf, token)
        response = await client.patch(
            f"/api/tenants/{org_fixture.tenant}", json={"name": "Body Version"}, headers=headers
        )
    assert response.status_code == 200, response.text
