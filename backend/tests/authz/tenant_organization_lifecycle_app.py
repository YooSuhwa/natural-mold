"""App-local mount of the organization archive/restore router (I21/I23 QA only).

The router is deliberately absent from ``app.router_registry`` until parent
Task 14; this fixture mounts it on a private app instance.
"""

from collections.abc import AsyncGenerator

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from app.dependencies import get_db
from app.main import create_app
from app.routers.tenant_organization_lifecycle import router as lifecycle_router
from app.routers.tenant_organizations import router as organizations_router
from tests import conftest as shared
from tests.authz.registration_fixture import seed_registration_default


@pytest.fixture
async def lifecycle_client() -> AsyncGenerator[AsyncClient, None]:
    app = create_app()
    paths = {route.path for route in app.routes if isinstance(route, APIRoute)}
    if "/api/tenants/{tenant_id}/organizations" not in paths:
        app.include_router(organizations_router)
    app.include_router(lifecycle_router)
    app.dependency_overrides[get_db] = shared.override_get_db
    async with shared.TestSession() as db:
        await seed_registration_default(db)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()
