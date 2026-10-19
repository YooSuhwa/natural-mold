"""Organization archive/restore client fixture (I21/I23 QA).

The router is globally registered; this fixture serves it from a private app
instance with the shared test database.
"""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from app.dependencies import get_db
from app.main import create_app
from tests import conftest as shared
from tests.authz.registration_fixture import seed_registration_default


@pytest.fixture
async def lifecycle_client() -> AsyncGenerator[AsyncClient, None]:
    app = create_app()
    app.dependency_overrides[get_db] = shared.override_get_db
    async with shared.TestSession() as db:
        await seed_registration_default(db)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()
