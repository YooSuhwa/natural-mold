"""D-09 structure: every route policy is an allowed kind naming real model relations.

The parametrised family matrices (``test_matrix_*.py``) prove the 8.4 status contract
on representative routes; this walk keeps every other route's declaration honest: the
``RES``/``CANDIDATES`` type and permissions exist in ``model.json``, ``CAP`` names an
organization capability, ``KEY`` an agent permission and ``PRIVATE`` a real table.
"""

from typing import Final

import pytest
from fastapi.routing import APIRoute

from app.authz.model_document import model_document
from app.authz.policies import (
    CandidatesPolicy,
    CapabilityPolicy,
    KeyPolicy,
    PrivatePolicy,
    ResourcePolicy,
    RoutePolicy,
    policy_of,
)
from app.config import settings
from app.database import Base
from app.main import create_app

ALLOWED: Final = frozenset(
    {"PUBLIC", "SELF", "CATALOG", "SUPER", "GOVERNANCE", "E2E_ONLY"}
    | {"RES", "CAP", "PRIVATE", "KEY", "SHARE_TOKEN", "CANDIDATES"}
)
# Health routes pick the type from ``target_kind``; the decorator names the MCP-server
# permission, the model branch is decided in-route as ``llm_model#can_use`` (9.5).
# ``DELETE /api/grants/{grant_id}`` decides on the grant's resource type (E-01).
DYNAMIC_TYPES: Final = {
    "{target_kind}": ("mcp_server",),
    "{resource_type}": ("agent", "skill", "tool", "mcp_server", "credential"),
}


def _relations() -> dict[str, frozenset[str]]:
    found: dict[str, frozenset[str]] = {}
    for definition in model_document().type_definitions:
        relations = definition.get("relations") or {}
        assert isinstance(relations, dict)
        found[str(definition["type"])] = frozenset(relations)
    return found


@pytest.fixture(scope="module")
def routes() -> list[tuple[str, APIRoute, RoutePolicy | None]]:
    previous = settings.e2e_test_helpers_enabled
    settings.e2e_test_helpers_enabled = True  # the opt-in helpers are inventoried too
    try:
        app = create_app()
    finally:
        settings.e2e_test_helpers_enabled = previous
    return [
        (f"{method} {route.path}", route, policy_of(route.endpoint))
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in sorted(route.methods)
    ]


def _problems(route: APIRoute, found: RoutePolicy, model: dict[str, frozenset[str]]) -> list[str]:
    match found:
        case ResourcePolicy(type_=type_, permission=permission, param=param):
            params = {p.name for p in (*route.dependant.path_params, *route.dependant.query_params)}
            types = DYNAMIC_TYPES.get(type_, (type_,))
            needed = (permission, found.min_visible)
            bad = [f"{t}#{n}" for t in types for n in needed if n not in model.get(t, ())]
            return bad + ([] if param in params else [f"param {param}"])
        case CandidatesPolicy(type_=type_, permission=permission):
            return [] if permission in model.get(type_, ()) else [f"{type_}#{permission}"]
        case CapabilityPolicy(capability=capability):
            ok = capability.startswith("cap_") and capability in model["organization"]
            return [] if ok else [f"organization#{capability}"]
        case KeyPolicy(permission=permission):
            return [] if permission in model["agent"] else [f"agent#{permission}"]
        case PrivatePolicy(model=table, param=param):
            params = {p.name for p in route.dependant.path_params}
            bad = [] if table in Base.metadata.tables else [f"table {table}"]
            return bad + ([] if param is None or param in params else [f"param {param}"])
        case _:
            return []


def test_every_route_policy_is_an_allowed_reviewed_kind(routes) -> None:
    kinds = {identity: None if found is None else found.kind for identity, _, found in routes}
    assert {identity: kind for identity, kind in kinds.items() if kind not in ALLOWED} == {}
    assert len(kinds) > 300  # the walk saw the whole application, not an empty app


def test_every_policy_names_types_and_relations_of_the_authz_model(routes) -> None:
    model = _relations()
    problems = {
        identity: issues
        for identity, route, found in routes
        if found is not None and (issues := _problems(route, found, model))
    }
    assert problems == {}
