"""Real cookie authentication exposes mode flags and runs company request dependencies."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from fastapi import Depends, FastAPI, Request
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.config import settings
from app.dependencies import CurrentUser, get_tenant_context, require_saas_platform_operator
from app.models.audit_event import AuditEvent
from app.models.organization import Tenant
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services import audit_service
from app.tenancy.context import TenantContext
from app.tenancy.deployment import DeploymentMode
from tests.conftest import TestSession

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


@pytest.fixture(autouse=True)
def isolated_company_api(
    raw_test_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    routes = list(raw_test_app.router.routes)
    monkeypatch.setattr(audit_service, "async_session", TestSession)
    try:
        yield
    finally:
        raw_test_app.router.routes[:] = routes


@pytest.mark.parametrize("mode", ["single_tenant", "multi_tenant"])
@pytest.mark.parametrize("operator", [False, True])
async def test_installation_flags_use_mode_and_fresh_user_authority(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
    monkeypatch: pytest.MonkeyPatch,
    mode: DeploymentMode,
    operator: bool,
) -> None:
    monkeypatch.setattr(settings, "deployment_mode", mode)
    monkeypatch.setattr(settings, "org_multi_enabled", False)
    # The JWT still carries the original operator claim; DB is the authority.
    await db.execute(
        update(User).where(User.id == org_fixture.user.id).values(is_super_user=operator)
    )
    await db.commit()
    response = await raw_client.get("/api/me/installation")
    assert response.status_code == 200
    assert response.json() == {
        "deployment_mode": mode,
        "org_multi_enabled": False,
        "platform_tenant_management_enabled": mode == "multi_tenant" and operator,
    }


async def test_installation_flags_require_authentication(raw_client: AsyncClient) -> None:
    response = await raw_client.get("/api/me/installation")
    assert response.status_code == 401


async def test_company_request_context_tracks_selected_company_and_suspension(
    raw_test_app: FastAPI,
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    @raw_test_app.get("/test/company-context")
    async def current_company(
        request: Request,
        context: TenantContext = Depends(get_tenant_context),
    ) -> dict[str, UUID]:
        assert request.state.tenant_context is context
        return {"tenant_id": context.tenant_id, "user_id": context.user_id}

    raw_client.cookies.set(ACTIVE_ORG_COOKIE, str(org_fixture.first))
    response = await raw_client.get("/test/company-context")
    assert response.status_code == 200
    assert response.json()["tenant_id"] == str(org_fixture.tenant)
    tokens = list(
        (
            await db.scalars(
                select(RefreshToken).where(RefreshToken.user_id == org_fixture.user.id)
            )
        ).all()
    )
    assert tokens and all(token.revoked_at is None for token in tokens)
    await db.execute(
        update(Tenant).where(Tenant.id == org_fixture.tenant).values(status="suspended")
    )
    await db.commit()
    response = await raw_client.get("/test/company-context")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "tenant_suspended"
    event = await db.scalar(
        select(AuditEvent).where(
            AuditEvent.reason_code == "tenant_suspended",
            AuditEvent.target_id == "/test/company-context",
        )
    )
    assert event is not None
    assert event.outcome == "denied" and event.actor_user_id == org_fixture.user.id
    await db.refresh(tokens[0])
    assert tokens[0].revoked_at is None
    await db.execute(update(Tenant).where(Tenant.id == org_fixture.tenant).values(status="active"))
    await db.commit()
    assert (await raw_client.get("/test/company-context")).status_code == 200


@pytest.mark.parametrize("mode", ["single_tenant", "multi_tenant"])
@pytest.mark.parametrize("operator", [False, True])
async def test_platform_management_dependency_is_saas_operator_only(
    raw_test_app: FastAPI,
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
    monkeypatch: pytest.MonkeyPatch,
    mode: DeploymentMode,
    operator: bool,
) -> None:
    @raw_test_app.get("/test/platform-company-management")
    async def platform_company(
        user: CurrentUser = Depends(require_saas_platform_operator),
    ) -> dict[str, UUID]:
        return {"operator_id": user.id}

    monkeypatch.setattr(settings, "deployment_mode", mode)
    await db.execute(
        update(User).where(User.id == org_fixture.user.id).values(is_super_user=operator)
    )
    await db.commit()
    response = await raw_client.get("/test/platform-company-management")
    expected = 404 if mode == "single_tenant" else 200 if operator else 403
    assert response.status_code == expected
