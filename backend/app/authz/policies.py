"""Explicit route policy markers during migration; no default for new endpoints.

The initial reviewed manifest attaches LEGACY/E2E_ONLY markers at registration.
P3 decorators replace them with enforcement policies. These markers themselves
do not authorize a request or bypass the existing authentication/CSRF guards.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Final, Literal, ParamSpec, TypeVar

from fastapi import FastAPI
from fastapi.routing import APIRoute

PolicyKind = Literal["LEGACY", "E2E_ONLY", "PUBLIC", "SELF", "CATALOG", "SUPER"]
LEGACY: Final = "LEGACY"
E2E_ONLY: Final = "E2E_ONLY"
PUBLIC: Final = "PUBLIC"
SELF: Final = "SELF"
CATALOG: Final = "CATALOG"
SUPER: Final = "SUPER"
P = ParamSpec("P")
R = TypeVar("R")


def authz_policy(policy: PolicyKind) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Attach a reviewed policy without wrapping or changing the signature."""

    def declare(endpoint: Callable[P, R]) -> Callable[P, R]:
        endpoint.__authz_policy__ = policy
        return endpoint

    return declare


def register_legacy_policies(app: FastAPI) -> None:
    """Classify only frozen baseline identities; unknown routes remain unmarked."""
    identities = frozenset(Path(__file__).with_name("legacy_routes.txt").read_text().splitlines())
    for route in app.routes:
        if not isinstance(route, APIRoute) or hasattr(route.endpoint, "__authz_policy__"):
            continue
        if all(f"{method} {route.path}" in identities for method in route.methods):
            authz_policy(LEGACY)(route.endpoint)
        elif route.endpoint.__module__ in {
            "app.routers.e2e_chat_run_helpers",
            "app.routers.e2e_mcp_apps_helpers",
        }:
            # E2E helpers also use an exact manifest; newly added helpers are not exempt.
            e2e_identities = frozenset(
                Path(__file__).with_name("e2e_routes.txt").read_text().splitlines()
            )
            if all(f"{method} {route.path}" in e2e_identities for method in route.methods):
                authz_policy(E2E_ONLY)(route.endpoint)
