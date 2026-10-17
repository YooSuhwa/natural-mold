"""Reviewed B11/B12 route inventory and its assembled-application violation scan."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from pydantic import JsonValue

from app.authz.policies import policy_kind
from app.dependencies import (
    get_org_context,
    get_tenant_context,
    owned_conversation,
    require_saas_platform_operator,
    verify_csrf,
)


@dataclass(frozen=True, slots=True)
class CompanyRoute:
    policy: str
    status: int
    if_match: bool


_T: Final = "/api/tenants/{tenant_id}"
_A: Final = "/api/admin/tenants"
_R: Final = "/api/orgs/{org_id}/retention"
COMPANY_PREFIXES: Final = ("/api/tenants/", "/api/admin/tenants", _R)
B11_ROUTES: Final[dict[str, CompanyRoute]] = {
    f"GET {_T}": CompanyRoute("SELF", 200, False),
    f"PATCH {_T}": CompanyRoute("SELF", 200, True),
    f"GET {_T}/organizations": CompanyRoute("SELF", 200, False),
    f"POST {_T}/organizations": CompanyRoute("SELF", 201, False),
    f"PATCH {_T}/organizations/{{org_id}}": CompanyRoute("SELF", 200, True),
    f"POST {_T}/organizations/{{org_id}}/archive": CompanyRoute("SELF", 200, True),
    f"POST {_T}/organizations/{{org_id}}/restore": CompanyRoute("SELF", 200, True),
    f"GET {_T}/members": CompanyRoute("SELF", 200, False),
    f"POST {_T}/members": CompanyRoute("SELF", 201, True),
    f"PATCH {_T}/members/{{user_id}}": CompanyRoute("SELF", 200, True),
    f"DELETE {_T}/members/{{user_id}}": CompanyRoute("SELF", 204, True),
    f"GET {_T}/roles": CompanyRoute("SELF", 200, False),
    f"PUT {_T}/roles/{{user_id}}": CompanyRoute("SELF", 200, True),
    f"GET {_T}/invitations": CompanyRoute("SELF", 200, False),
    f"POST {_T}/invitations": CompanyRoute("SELF", 201, True),
    f"POST {_T}/invitations/{{invitation_id}}/resend": CompanyRoute("SELF", 201, True),
    f"DELETE {_T}/invitations/{{invitation_id}}": CompanyRoute("SELF", 204, True),
}
# B12 reads If-Match from the request inside platform_mutation, so OpenAPI has no header.
B12_ROUTES: Final[dict[str, CompanyRoute]] = {
    f"GET {_A}": CompanyRoute("SUPER", 200, False),
    f"POST {_A}": CompanyRoute("SUPER", 201, False),
    f"GET {_A}/{{tenant_id}}": CompanyRoute("SUPER", 200, False),
    f"PATCH {_A}/{{tenant_id}}": CompanyRoute("SUPER", 200, False),
    f"POST {_A}/{{tenant_id}}/suspend": CompanyRoute("SUPER", 200, False),
    f"POST {_A}/{{tenant_id}}/resume": CompanyRoute("SUPER", 200, False),
}
# E-09 company publication policy and approval queue (9.7, S05).
E09_ROUTES: Final[dict[str, CompanyRoute]] = {
    f"GET {_T}/policies": CompanyRoute("SELF", 200, False),
    f"PUT {_T}/policies": CompanyRoute("GOVERNANCE", 200, True),
    f"GET {_T}/publication-requests": CompanyRoute("GOVERNANCE", 200, False),
    f"POST {_T}/publication-requests/{{item_id}}/approve": CompanyRoute("GOVERNANCE", 200, False),
    f"POST {_T}/publication-requests/{{item_id}}/reject": CompanyRoute("GOVERNANCE", 200, False),
}
# E-07 company settings tier: models, shared credentials, catalog policy, service LLM.
_G: Final = "GOVERNANCE"
E07_ROUTES: Final[dict[str, CompanyRoute]] = {
    f"GET {_T}/catalog-policy": CompanyRoute(_G, 200, False),
    f"PUT {_T}/catalog-policy": CompanyRoute(_G, 200, True),
    f"GET {_T}/llm-settings": CompanyRoute(_G, 200, False),
    f"PUT {_T}/llm-settings": CompanyRoute(_G, 200, False),
    f"GET {_T}/models": CompanyRoute(_G, 200, False),
    f"POST {_T}/models": CompanyRoute(_G, 201, False),
    f"PATCH {_T}/models/{{model_id}}": CompanyRoute(_G, 200, False),
    f"DELETE {_T}/models/{{model_id}}": CompanyRoute(_G, 204, False),
    f"GET {_T}/credentials": CompanyRoute(_G, 200, False),
    f"POST {_T}/credentials": CompanyRoute(_G, 201, False),
    f"PATCH {_T}/credentials/{{credential_id}}": CompanyRoute(_G, 200, False),
    f"DELETE {_T}/credentials/{{credential_id}}": CompanyRoute(_G, 204, False),
}
# E-08 organization retention policy (9.7, S08); audit retention rides E-09 /policies.
E08_ROUTES: Final[dict[str, CompanyRoute]] = {
    f"GET {_R}": CompanyRoute("SELF", 200, False),
    f"PUT {_R}": CompanyRoute("GOVERNANCE", 200, True),
}
COMPANY_ROUTES: Final = {**B11_ROUTES, **B12_ROUTES, **E09_ROUTES, **E07_ROUTES, **E08_ROUTES}
# Later-task paths from the contract's route-level crosswalk (rows 7-8, 24-35, 38-57),
# owned by C05/E07/E08/E09/H03/H06/H08/I07. Matched app-wide by path, with parameter
# names erased, at the path itself or below it (CRUD collection/item routes).
DEFERRED_PATHS: Final = frozenset(
    {
        "/api/admin/tenants/{}/export",
        "/api/admin/tenants/{}/exports",
        "/api/admin/tenants/{}/schedule-deletion",
        "/api/admin/tenants/{}/cancel-deletion",
        "/api/admin/authz",
        "/api/tenants/{}/usage",
        "/api/tenants/{}/audit-events",
        "/api/tenants/{}/limits",
        "/api/tenants/{}/domains",
        "/api/tenants/{}/sso-connections",
        "/api/usage-limits",
        "/api/me/usage-limits",
        "/api/auth/sso",
        "/api/auth/ldap",
        "/auth/callback",
    }
)
_PARAM: Final = re.compile(r"\{[^}]*\}")
_OPERATOR_CONTENT: Final[frozenset[Callable[..., object]]] = frozenset(
    {get_org_context, get_tenant_context, owned_conversation}
)
_TENANT_SLUG: Final[dict[str, JsonValue]] = {
    "maxLength": 40,
    "minLength": 3,
    "pattern": "^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$",
    "title": "Slug",
    "type": "string",
}
_TENANT_SLUG_SCHEMAS: Final = ("PlatformTenantCreate", "TenantOverview", "TenantAdminOverview")
_ORGANIZATION_SLUG_SCHEMAS: Final = ("TenantOrganizationSummary",)


def _calls(dependant: Dependant) -> Iterable[Callable[..., object]]:
    for child in dependant.dependencies:
        if child.call is not None:
            yield child.call
        yield from _calls(child)


def _headers(dependant: Dependant) -> set[str]:
    found = {param.alias for param in dependant.header_params}
    for child in dependant.dependencies:
        found |= _headers(child)
    return found


def _legacy_identities() -> frozenset[str]:
    baseline = Path(__file__).resolve().parents[2] / "app/authz/legacy_routes.txt"
    return frozenset(baseline.read_text().splitlines())


def _route_violations(identity: str, route: APIRoute, expected: CompanyRoute) -> list[str]:
    method = identity.split(" ", 1)[0]
    calls = set(_calls(route.dependant))
    found: list[str] = []
    if policy_kind(route.endpoint) != expected.policy:
        found.append(f"policy:{identity}")
    if (route.status_code or 200) != expected.status:
        found.append(f"status:{identity}")
    if expected.status != 204 and route.response_model is None:
        found.append(f"response_model:{identity}")
    if method != "GET" and verify_csrf not in calls:
        found.append(f"csrf:{identity}")
    if expected.if_match and "If-Match" not in _headers(route.dependant):
        found.append(f"if_match:{identity}")
    if expected.policy == "SUPER" and (
        require_saas_platform_operator not in calls or calls & _OPERATOR_CONTENT
    ):
        found.append(f"operator_content:{identity}")
    return found


def is_deferred(path: str) -> bool:
    """A later-task path (or a route below it), whatever its parameter names."""
    shape = _PARAM.sub("{}", path)
    return any(shape == item or shape.startswith(f"{item}/") for item in DEFERRED_PATHS)


def company_route_violations(routes: Iterable[object]) -> list[str]:
    """Return every inventory, policy, CSRF, schema and deferral violation."""
    legacy = _legacy_identities()
    seen: Counter[str] = Counter()
    found: list[str] = []
    for route in routes:
        path = getattr(route, "path", None)
        deferred = isinstance(path, str) and is_deferred(path)
        if deferred and not isinstance(route, APIRoute):
            found.append(f"deferred:{type(route).__name__} {path}")
        if not isinstance(route, APIRoute) or not (
            deferred or route.path.startswith(COMPANY_PREFIXES)
        ):
            continue
        for method in sorted(route.methods):
            identity = f"{method} {route.path}"
            seen[identity] += 1
            if identity in legacy or policy_kind(route.endpoint) == "LEGACY":
                found.append(f"legacy:{identity}")
            if deferred:
                found.append(f"deferred:{identity}")
            elif identity not in COMPANY_ROUTES:
                found.append(f"unclassified:{identity}")
            else:
                found.extend(_route_violations(identity, route, COMPANY_ROUTES[identity]))
    found.extend(f"missing:{identity}" for identity in COMPANY_ROUTES if seen[identity] == 0)
    found.extend(f"duplicate:{identity}" for identity, count in seen.items() if count > 1)
    return sorted(found)


def slug_schema_violations(schemas: dict[str, JsonValue]) -> list[str]:
    """Tenant slugs keep the public 3-40 contract; organization slugs stay separate."""
    found: list[str] = []
    for name in _TENANT_SLUG_SCHEMAS + _ORGANIZATION_SLUG_SCHEMAS:
        schema = schemas.get(name)
        properties = schema.get("properties") if isinstance(schema, dict) else None
        slug = properties.get("slug") if isinstance(properties, dict) else None
        if name in _TENANT_SLUG_SCHEMAS and slug != _TENANT_SLUG:
            found.append(f"tenant_slug:{name}")
        if name in _ORGANIZATION_SLUG_SCHEMAS and (
            not isinstance(slug, dict) or "maxLength" in slug or "pattern" in slug
        ):
            found.append(f"organization_slug:{name}")
    return found
