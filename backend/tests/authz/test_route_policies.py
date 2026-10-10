"""Every live route declares a policy, including opt-in E2E helper routers."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.authz.policies import register_legacy_policies
from app.config import settings
from app.main import create_app

BASELINE = Path(__file__).with_name("legacy_routes.txt")


@pytest.fixture
def all_routes(monkeypatch):
    # Given: conditional E2E routes are included in the same inventory.
    monkeypatch.setattr(settings, "e2e_test_helpers_enabled", True)
    app = create_app()
    return [route for route in app.routes if isinstance(route, APIRoute)]


def test_every_route_declares_policy_when_all_routers_are_enabled(all_routes):
    # When: all endpoints are inspected.
    missing = [
        route.path for route in all_routes if not hasattr(route.endpoint, "__authz_policy__")
    ]
    # Then: policy omission cannot ship.
    assert missing == []


def test_legacy_route_set_cannot_grow_when_an_endpoint_is_added(all_routes):
    # Given: a reviewed list of existing legacy routes.
    allowed = set(BASELINE.read_text().splitlines())
    # When: the actual legacy routes are enumerated.
    legacy = {
        f"{method} {route.path}"
        for route in all_routes
        if getattr(route.endpoint, "__authz_policy__", None) == "LEGACY"
        for method in route.methods
    }
    # Then: replacing an old route with a new legacy route also fails, even at equal counts.
    assert legacy <= allowed
    assert len(legacy) <= 275


def test_e2e_helpers_have_distinct_policy_when_enabled(all_routes):
    # When: test-only routes are inspected.
    helpers = [route for route in all_routes if route.path.startswith("/api/e2e/")]
    # Then: every helper stays identifiable as test-only.
    assert len(helpers) == 5
    assert all(getattr(route.endpoint, "__authz_policy__", None) == "E2E_ONLY" for route in helpers)


def test_missing_policy_is_detectable_when_new_route_is_registered():
    # Given: a developer adds a route without a policy.
    app = FastAPI()

    @app.get("/unclassified")
    async def unclassified() -> bool:
        return True

    # When: the same inventory guard runs.
    register_legacy_policies(app)
    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    # Then: registration does not silently assign a default policy.
    assert [r.path for r in routes if not hasattr(r.endpoint, "__authz_policy__")] == [
        "/unclassified"
    ]
