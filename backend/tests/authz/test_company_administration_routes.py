"""The assembled application registers exactly the reviewed B11/B12 route contract."""

from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from pydantic import JsonValue, TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token, create_csrf_token
from app.authz.policies import LEGACY, SUPER, authz_policy
from app.config import settings
from app.dependencies import get_db, get_org_context, require_saas_platform_operator
from app.main import create_app
from app.models.user import User
from app.schemas.admin_tenant import AdminTenantDetail
from app.services import audit_service
from tests.authz.company_route_inventory import (
    COMPANY_ROUTES,
    company_route_violations,
    slug_schema_violations,
)
from tests.authz.tenant_organizations_fixture import seed_task7_case
from tests.conftest import TestSession

pytest_plugins = ["tests.authz.tenant_organizations_fixture"]
SCHEMAS = TypeAdapter(dict[str, JsonValue])
SUSPEND = "/api/admin/tenants/{tenant_id}/suspend"


def _schemas(app: FastAPI) -> dict[str, JsonValue]:
    components = app.openapi()["components"]
    return SCHEMAS.validate_python(components["schemas"])


def test_assembled_app_registers_exact_company_inventory_once() -> None:
    app = create_app()
    assert company_route_violations(app.routes) == []
    assert len(COMPANY_ROUTES) == 72  # +7 H-06, +17 H-08, +5 H-04
    assert slug_schema_violations(_schemas(app)) == []


def test_inventory_rejects_unclassified_deferred_and_legacy_routes() -> None:
    app = create_app()
    extra = APIRouter()

    @extra.get("/api/admin/tenants/{tenant_id}/export")
    @authz_policy(SUPER)
    async def deferred_export() -> bool:
        return True

    @extra.get("/api/admin/tenants/{tenant_id}/unreviewed")
    async def unreviewed() -> bool:
        return True

    @extra.get("/api/tenants/{tenant_id}/legacy-probe")
    @authz_policy(LEGACY)
    async def legacy_probe() -> bool:
        return True

    app.include_router(extra)
    found = company_route_violations(app.routes)
    assert found == [
        "deferred:GET /api/admin/tenants/{tenant_id}/export",
        "legacy:GET /api/tenants/{tenant_id}/legacy-probe",
        "unclassified:GET /api/admin/tenants/{tenant_id}/unreviewed",
        "unclassified:GET /api/tenants/{tenant_id}/legacy-probe",
    ]


def test_inventory_rejects_later_task_paths_app_wide() -> None:
    app = FastAPI()
    paths = (
        "/api/admin/tenants/{id}/exports",
        "/api/admin/tenants/{tenant_id}/exports/{export_id}",
    )
    for path in (*paths, "/api/usage-limits-history", "/api/auth/login"):
        app.add_api_route(path, lambda: True, methods=["GET"])

    found = [item for item in company_route_violations(app.routes) if item.startswith("deferred")]
    assert found == sorted(f"deferred:GET {path}" for path in paths)


def test_inventory_rejects_missing_csrf_response_model_and_operator_content() -> None:
    app = FastAPI()

    @app.post(SUSPEND, response_model=None, dependencies=[Depends(get_org_context)])
    @authz_policy(SUPER)
    async def suspend_without_guards() -> None:
        return None

    found = company_route_violations(app.routes)
    assert {
        f"csrf:POST {SUSPEND}",
        f"response_model:POST {SUSPEND}",
        f"operator_content:POST {SUSPEND}",
    } <= set(found)
    assert "missing:GET /api/admin/tenants" in found


def test_inventory_rejects_duplicate_and_wrong_policy() -> None:
    app = FastAPI()

    for _ in range(2):

        @app.post(
            SUSPEND,
            response_model=AdminTenantDetail,
            dependencies=[Depends(require_saas_platform_operator)],
        )
        async def suspend_legacy() -> bool:
            return True

    found = company_route_violations(app.routes)
    assert f"duplicate:POST {SUSPEND}" in found
    assert f"policy:POST {SUSPEND}" in found


def test_slug_schema_drift_is_rejected() -> None:
    schemas = _schemas(create_app())
    drifted = SCHEMAS.validate_python(
        {
            **schemas,
            "PlatformTenantCreate": {"properties": {"slug": {"type": "string", "maxLength": 41}}},
            "TenantOrganizationSummary": {
                "properties": {"slug": {"type": "string", "maxLength": 40}}
            },
        }
    )
    assert slug_schema_violations(drifted) == [
        "tenant_slug:PlatformTenantCreate",
        "organization_slug:TenantOrganizationSummary",
    ]


@pytest.fixture
async def operator_client(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[tuple[AsyncClient, dict[str, str]], None]:
    monkeypatch.setattr(settings, "deployment_mode", "multi_tenant")
    monkeypatch.setattr(audit_service, "async_session", TestSession)
    operator = User(
        id=uuid4(),
        email="task14-operator@example.com",
        name="Task 14 Operator",
        hashed_password="unused",
        is_active=True,
        is_super_user=True,
    )
    db.add(operator)
    await db.commit()
    app = create_app()
    assert sum(isinstance(r, APIRoute) and r.path == SUSPEND for r in app.routes) == 1

    async def current_session() -> AsyncGenerator[AsyncSession, None]:
        yield db

    app.dependency_overrides[get_db] = current_session
    csrf = create_csrf_token(operator.id)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set(
            settings.cookie_name_access, create_access_token(operator.id, is_super_user=True)
        )
        client.cookies.set(settings.cookie_name_csrf, csrf)
        yield client, {"X-CSRF-Token": csrf}


def _company(slug: str) -> dict[str, str | int | bool]:
    return {
        "name": "Task 14 Company",
        "slug": slug,
        "first_admin_email": "task14-admin@example.com",
        "first_organization_name": "Task 14 Organization",
        "member_limit": 5,
        "monthly_spend_limit": "100.00",
        "concurrent_run_limit": 2,
        "allow_system_models": False,
    }


@pytest.mark.parametrize(
    ("slug", "status"),
    [("ab", 422), ("a" * 41, 422), ("-abc", 422), ("abc-", 422), ("abc", 201), ("a" * 40, 201)],
)
async def test_assembled_tenant_creation_enforces_slug_boundaries(
    operator_client: tuple[AsyncClient, dict[str, str]], slug: str, status: int
) -> None:
    client, headers = operator_client
    response = await client.post("/api/admin/tenants", json=_company(slug), headers=headers)
    assert response.status_code == status, response.text


async def test_assembled_generated_organization_slug_ignores_tenant_limit(
    db: AsyncSession, task7_client: AsyncClient
) -> None:
    case = await seed_task7_case(task7_client, db)
    name = "Organization name that is longer than any tenant slug"
    response = await task7_client.post(
        f"/api/tenants/{case.tenant_id}/organizations",
        json={"name": name, "default_member_role": "member"},
        headers=case.auth.headers(),
    )
    assert response.status_code == 201, response.text
    slug = response.json()["organization"]["slug"]
    assert slug == "organization-name-that-is-longer-than-any-tenant-slug"
    assert len(slug) > 40
