"""Service query and transaction boundary for platform tenant metadata."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import replace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.auth.jwt import create_csrf_token
from app.config import settings
from app.dependencies import CurrentUser, get_current_user, get_db
from app.exception_handlers import register_exception_handlers
from app.models.audit_event import AuditEvent
from app.models.organization import Tenant
from app.routers.platform_tenant_directory import router
from app.schemas.admin_tenant import PlatformTenantMetadataPatch
from app.services import audit_service
from app.services.platform.tenant_directory import (
    PlatformTenantDirectoryQuery,
    PlatformTenantMutation,
    get_platform_tenant,
    list_platform_tenants,
    update_platform_tenant,
)
from app.services.tenants.versioning import etag

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


def _mutation_app(db: AsyncSession, actor: CurrentUser) -> FastAPI:
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


async def test_patch_requires_csrf_before_tenant_lookup(
    db: AsyncSession,
    org_fixture: OrgFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "multi_tenant")
    app = _mutation_app(db, replace(org_fixture.user, is_super_user=True))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(
            "/api/admin/tenants/00000000-0000-0000-0000-000000000099",
            json={"name": "Rejected"},
        )

    assert response.status_code == 403 and response.json()["error"]["code"] == "csrf_mismatch"


@pytest.mark.parametrize(
    ("missing_target", "if_match"),
    [
        (False, None),
        (False, "not-an-etag"),
        (False, '"2000-01-01T00:00:00+00:00"'),
        (True, None),
    ],
)
async def test_patch_denials_do_not_autoflush_caller_state_or_audit(
    db: AsyncSession,
    org_fixture: OrgFixture,
    monkeypatch: pytest.MonkeyPatch,
    missing_target: bool,
    if_match: str | None,
) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "multi_tenant")
    actor = replace(org_fixture.user, is_super_user=True)
    target = await db.get(Tenant, org_fixture.tenant)
    assert target is not None
    target_id = target.id
    pending = await db.scalar(select(Tenant).where(Tenant.id != target_id))
    assert pending is not None
    pending_id = pending.id
    original_name = pending.name
    original_version = pending.updated_at
    pending.name = "caller-pending-state"
    token = create_csrf_token(actor.id)
    headers = {"X-CSRF-Token": token}
    if missing_target:
        headers["If-Match"] = etag(target.updated_at)
    if not missing_target and if_match is not None:
        headers["If-Match"] = if_match
    app = _mutation_app(db, actor)
    flushes = 0

    def count_flush(_session, _flush_context, _instances) -> None:
        nonlocal flushes
        flushes += 1

    event.listen(db.sync_session, "before_flush", count_flush)
    request_target = "00000000-0000-0000-0000-000000000099" if missing_target else target_id

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            client.cookies.set(settings.cookie_name_csrf, token)
            response = await client.patch(
                f"/api/admin/tenants/{request_target}",
                headers=headers,
                json={"name": "Rejected"},
            )
    finally:
        event.remove(db.sync_session, "before_flush", count_flush)

    assert response.status_code == (404 if missing_target else 409)
    assert flushes == 0
    assert pending in db.dirty
    assert pending.updated_at == original_version
    await db.rollback()
    refreshed = await db.get(Tenant, pending_id, populate_existing=True)
    assert refreshed is not None and refreshed.name == original_name
    assert (
        await db.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "tenant.update")
        )
        == 0
    )


async def test_directory_queries_only_metadata_and_aggregate_tables(
    db: AsyncSession, org_fixture: OrgFixture
) -> None:
    statements: list[str] = []
    bind = db.get_bind()

    def capture_statement(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany: bool,
    ) -> None:
        statements.append(statement.lower())

    event.listen(bind, "before_cursor_execute", capture_statement)
    try:
        page = await list_platform_tenants(
            db,
            PlatformTenantDirectoryQuery(
                status=None,
                search=None,
                cursor=None,
                limit=50,
            ),
        )
        detail = await get_platform_tenant(db, org_fixture.tenant)
    finally:
        event.remove(bind, "before_cursor_execute", capture_statement)

    assert page.items and detail.id == org_fixture.tenant
    rendered = "\n".join(statements)
    assert all(table in rendered for table in ("tenants", "organizations", "tenant_members"))
    assert all(
        column not in rendered
        for column in (
            "tenants.settings",
            "tenants.secrets_namespace",
            "tenants.data_keys",
            "tenants.deletion_due_at",
        )
    )
    assert all(
        table not in rendered
        for table in (
            "agents",
            "conversations",
            "message_events",
            "credentials",
            "tools",
            "skills",
            "organization_members",
            "tenant_role_grants",
        )
    )


async def test_update_rolls_back_metadata_and_audit_on_commit_failure(
    db: AsyncSession,
    org_fixture: OrgFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await db.get(Tenant, org_fixture.tenant)
    assert tenant is not None
    tenant.limits = {"member_limit": 2, "reserved": "keep"}
    await db.commit()
    await db.refresh(tenant)
    original_name = tenant.name
    original_limits = dict(tenant.limits)
    original_updated_at = tenant.updated_at
    tenant_id = tenant.id

    async def fail_commit() -> None:
        raise SQLAlchemyError("forced commit failure")

    monkeypatch.setattr(db, "commit", fail_commit)
    with pytest.raises(SQLAlchemyError, match="forced commit failure"):
        await update_platform_tenant(
            PlatformTenantMutation(
                db=db,
                request=Request(
                    {"type": "http", "method": "PATCH", "path": "/direct", "headers": []}
                ),
                actor=replace(org_fixture.user, is_super_user=True),
                tenant_id=tenant_id,
                expected_version=etag(original_updated_at),
            ),
            PlatformTenantMetadataPatch(name="Rolled Back", member_limit=9),
        )

    refreshed = await db.get(Tenant, tenant_id, populate_existing=True)
    assert refreshed is not None
    assert (refreshed.name, refreshed.limits, refreshed.updated_at) == (
        original_name,
        original_limits,
        original_updated_at,
    )
    assert (
        await db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "tenant.update", AuditEvent.target_id == str(tenant_id))
        )
        == 0
    )


async def test_update_rolls_back_when_audit_write_fails(
    db: AsyncSession,
    org_fixture: OrgFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await db.get(Tenant, org_fixture.tenant)
    assert tenant is not None
    await db.commit()
    original_name = tenant.name
    original_updated_at = tenant.updated_at
    tenant_id = tenant.id
    monkeypatch.setattr(
        audit_service,
        "record_event",
        AsyncMock(side_effect=SQLAlchemyError("forced audit failure")),
    )

    with pytest.raises(SQLAlchemyError, match="forced audit failure"):
        await update_platform_tenant(
            PlatformTenantMutation(
                db=db,
                request=Request(
                    {"type": "http", "method": "PATCH", "path": "/direct", "headers": []}
                ),
                actor=replace(org_fixture.user, is_super_user=True),
                tenant_id=tenant_id,
                expected_version=etag(original_updated_at),
            ),
            PlatformTenantMetadataPatch(name="Rolled Back"),
        )

    refreshed = await db.get(Tenant, tenant_id, populate_existing=True)
    assert refreshed is not None
    assert (refreshed.name, refreshed.updated_at) == (original_name, original_updated_at)
