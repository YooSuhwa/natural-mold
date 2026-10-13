"""Isolated HTTP contract for the SaaS operator company directory."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

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
from app.models.organization import Organization, Tenant, TenantMember
from app.routers.platform_tenant_directory import router
from app.routers.tenant_overview import router as tenant_overview_router
from app.services.tenants.versioning import etag

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


def _directory_app(db: AsyncSession, actor: CurrentUser) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.include_router(tenant_overview_router)

    async def session() -> AsyncGenerator[AsyncSession, None]:
        yield db

    async def current_user() -> CurrentUser:
        return actor

    app.dependency_overrides[get_db] = session
    app.dependency_overrides[get_current_user] = current_user
    return app


def _csrf_headers(actor: CurrentUser, current_etag: str | None) -> tuple[str, dict[str, str]]:
    token = create_csrf_token(actor.id)
    headers = {"X-CSRF-Token": token}
    if current_etag is not None:
        headers["If-Match"] = current_etag
    return token, headers


@pytest.mark.parametrize(
    ("mode", "operator", "expected_status"),
    [
        ("single_tenant", False, 404),
        ("single_tenant", True, 404),
        ("multi_tenant", False, 403),
        ("multi_tenant", True, 200),
    ],
)
async def test_directory_gate_is_saas_operator_only(
    db: AsyncSession,
    org_fixture: OrgFixture,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    operator: bool,
    expected_status: int,
) -> None:
    monkeypatch.setattr(settings, "deployment_mode", mode)
    actor = replace(org_fixture.user, is_super_user=operator)
    app = _directory_app(db, actor)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/admin/tenants")
        detail = await client.get(f"/api/admin/tenants/{org_fixture.tenant}")

    assert response.status_code == detail.status_code == expected_status


async def test_list_binds_stable_cursor_to_status_and_search(
    db: AsyncSession,
    org_fixture: OrgFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "multi_tenant")
    now = datetime(2026, 10, 13, tzinfo=UTC)
    tenants = [
        Tenant(
            name=f"Directory {index}",
            slug=f"directory-{index}",
            status="active" if index < 3 else "suspended",
            secrets_namespace=f"tenants/directory-{index}",
            settings={"private": f"setting-{index}"},
            data_keys={"private": f"key-{index}"},
            limits={"member_limit": index, "reserved": f"secret-{index}"},
            created_at=now + timedelta(minutes=index),
            updated_at=now + timedelta(minutes=index),
        )
        for index in range(1, 4)
    ]
    db.add_all(tenants)
    await db.flush()
    db.add_all(
        Organization(tenant_id=tenant.id, name=tenant.name, slug="default") for tenant in tenants
    )
    db.add_all(
        TenantMember(tenant_id=tenant.id, user_id=org_fixture.user.id) for tenant in tenants[:2]
    )
    await db.commit()
    app = _directory_app(db, replace(org_fixture.user, is_super_user=True))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get(
            "/api/admin/tenants", params={"status": "active", "search": "Directory", "limit": 1}
        )
        cursor = first.json()["next_cursor"]
        second = await client.get(
            "/api/admin/tenants",
            params={"status": "active", "search": "Directory", "limit": 1, "cursor": cursor},
        )
        replay = await client.get(
            "/api/admin/tenants",
            params={"status": "suspended", "search": "Directory", "limit": 1, "cursor": cursor},
        )
        slug_search = await client.get("/api/admin/tenants", params={"search": "directory-2"})

    assert first.status_code == second.status_code == slug_search.status_code == 200
    assert first.json()["has_more"] is True and cursor
    assert first.json()["items"][0]["id"] != second.json()["items"][0]["id"]
    assert slug_search.json()["items"][0]["slug"] == "directory-2"
    assert replay.status_code == 422
    assert replay.json()["error"]["code"] == "invalid_cursor"
    item = first.json()["items"][0]
    assert set(item) == {
        "id",
        "name",
        "slug",
        "status",
        "created_at",
        "updated_at",
        "limits",
        "organization_count",
        "active_member_count",
    }
    assert set(item["limits"]) == {
        "member_limit",
        "monthly_spend_limit",
        "concurrent_run_limit",
        "allow_system_models",
    }
    assert "private" not in first.text and "reserved" not in first.text


async def test_detail_and_patch_are_metadata_only_without_operator_membership(
    db: AsyncSession,
    org_fixture: OrgFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "multi_tenant")
    actor = replace(org_fixture.user, is_super_user=True)
    target = await db.scalar(
        select(Tenant)
        .join(Organization, Organization.tenant_id == Tenant.id)
        .where(Organization.id == org_fixture.foreign)
    )
    assert target is not None
    target.slug = "foreign-company"
    target.limits = {"member_limit": 2, "reserved": "keep-limit-secret"}
    target.settings = {"reserved": "keep-setting-secret"}
    target.data_keys = {"private": "keep-data-key"}
    target.secrets_namespace = "tenants/keep-namespace"
    await db.commit()
    await db.refresh(target)
    target_id = target.id
    original_etag = etag(target.updated_at)
    app = _directory_app(db, actor)
    token, headers = _csrf_headers(actor, original_etag)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        detail = await client.get(f"/api/admin/tenants/{target_id}")
        client.cookies.set(settings.cookie_name_csrf, token)
        updated = await client.patch(
            f"/api/admin/tenants/{target_id}",
            headers=headers,
            json={
                "name": "Renamed Foreign",
                "member_limit": 5,
                "monthly_spend_limit": "25.50",
                "concurrent_run_limit": 3,
                "allow_system_models": True,
            },
        )
        private = await client.get(f"/api/tenants/{target_id}")

    assert detail.status_code == updated.status_code == 200
    assert detail.headers["etag"] == original_etag
    assert updated.headers["etag"] != original_etag
    assert updated.json()["name"] == "Renamed Foreign"
    assert updated.json()["limits"] == {
        "member_limit": 5,
        "monthly_spend_limit": "25.50",
        "concurrent_run_limit": 3,
        "allow_system_models": True,
    }
    assert private.status_code == 404
    rendered = detail.text + updated.text
    assert all(
        hidden not in rendered
        for hidden in (
            "settings",
            "data_keys",
            "secrets_namespace",
            "keep-setting-secret",
            "keep-data-key",
            "keep-namespace",
            "keep-limit-secret",
        )
    )
    preserved = (
        await db.execute(
            select(
                Tenant.settings, Tenant.data_keys, Tenant.secrets_namespace, Tenant.limits
            ).where(Tenant.id == target_id)
        )
    ).one()
    assert preserved.settings == {"reserved": "keep-setting-secret"}
    assert preserved.data_keys == {"private": "keep-data-key"}
    assert preserved.secrets_namespace == "tenants/keep-namespace"
    assert preserved.limits["reserved"] == "keep-limit-secret"
    assert (
        await db.scalar(
            select(func.count())
            .select_from(TenantMember)
            .where(TenantMember.tenant_id == target_id)
        )
        == 0
    )
    audits = (
        await db.scalars(
            select(AuditEvent).where(
                AuditEvent.action == "tenant.update", AuditEvent.target_id == str(target_id)
            )
        )
    ).all()
    assert len(audits) == 1
    assert audits[0].actor_user_id == actor.id
    assert audits[0].event_metadata == {
        "tenant_id": str(target_id),
        "changed_fields": [
            "allow_system_models",
            "concurrent_run_limit",
            "member_limit",
            "monthly_spend_limit",
            "name",
        ],
    }


async def test_patch_requires_csrf_before_tenant_lookup(
    db: AsyncSession,
    org_fixture: OrgFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "multi_tenant")
    app = _directory_app(db, replace(org_fixture.user, is_super_user=True))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(
            "/api/admin/tenants/00000000-0000-0000-0000-000000000099",
            json={"name": "Rejected"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_mismatch"
