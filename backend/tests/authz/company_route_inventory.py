"""Reviewed B11/B12 route inventory and its assembled-application violation scan."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from pydantic import JsonValue

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
COMPANY_PREFIXES: Final = ("/api/tenants/", "/api/admin/tenants")
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
COMPANY_ROUTES: Final = {**B11_ROUTES, **B12_ROUTES}
# Canonical 9.7 rows owned by C05/E07/E08/E09/H03/H06/H08/I07 must stay unregistered.
DEFERRED_ROUTES: Final = frozenset(
    {
        f"POST {_A}/{{tenant_id}}/export",
        f"GET {_A}/{{tenant_id}}/exports",
        f"POST {_A}/{{tenant_id}}/schedule-deletion",
        f"POST {_A}/{{tenant_id}}/cancel-deletion",
        f"GET {_A}/{{tenant_id}}/authorization-status",
        f"POST {_A}/{{tenant_id}}/authorization/reconcile",
        f"GET {_T}/models",
        f"GET {_T}/credentials",
        f"GET {_T}/llm-settings",
        f"GET {_T}/catalog-policy",
        f"GET {_T}/usage",
        f"GET {_T}/audit-events",
        f"GET {_T}/limits",
        f"GET {_T}/domains",
        f"GET {_T}/sso-connections",
        f"GET {_T}/publication-requests",
        f"GET {_T}/publication-policy",
        f"GET {_T}/audit-retention-policy",
        f"GET {_T}/organizations/{{org_id}}/retention",
    }
)
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
    if getattr(route.endpoint, "__authz_policy__", None) != expected.policy:
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


def company_route_violations(routes: Iterable[object]) -> list[str]:
    """Return every inventory, policy, CSRF, schema and deferral violation."""
    legacy = _legacy_identities()
    seen: Counter[str] = Counter()
    found: list[str] = []
    for route in routes:
        if not isinstance(route, APIRoute) or not route.path.startswith(COMPANY_PREFIXES):
            continue
        for method in sorted(route.methods):
            identity = f"{method} {route.path}"
            seen[identity] += 1
            if identity in legacy or getattr(route.endpoint, "__authz_policy__", None) == "LEGACY":
                found.append(f"legacy:{identity}")
            if identity in DEFERRED_ROUTES:
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
