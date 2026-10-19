"""Cookie-authenticated mutations retain CSRF throughout authorization migration."""

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.main import create_app


def dependency_calls(dependant: Dependant) -> tuple[str, ...]:
    """Flatten dependency order, including inherited operator auth guards."""
    own = () if dependant.call is None else (getattr(dependant.call, "__name__", ""),)
    return own + tuple(name for child in dependant.dependencies for name in dependency_calls(child))


def test_cookie_mutations_require_csrf_when_registered():
    # Given: all production routes, including nested routers.
    routes = [route for route in create_app().routes if isinstance(route, APIRoute)]
    # When: cookie-authenticated mutations are inspected.
    missing = []
    for route in routes:
        if route.methods <= {"GET", "HEAD", "OPTIONS"}:
            continue
        calls = dependency_calls(route.dependant)
        if "get_current_user" in calls and "verify_csrf" not in calls:
            missing.extend(f"{method} {route.path}" for method in sorted(route.methods))
    # Then: switching authorization never removes CSRF protection.
    assert missing == []
