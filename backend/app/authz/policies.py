"""Explicit route policy markers (plan 8.3); no default for new endpoints.

Every marker is a frozen value of the tagged union ``RoutePolicy``; ``policy.kind``
names its variant, so inventories and ratchets match on ``kind`` alone. Bare kinds are
module constants (``SELF``, ``LEGACY`` ...); parameterised kinds come from the 8.3
factories (``RES``, ``CAP``, ``PRIVATE``, ``KEY``, ``SHARE_TOKEN``, ``CANDIDATES``).

The initial reviewed manifest attaches LEGACY/E2E_ONLY markers at registration.
P3 decorators replace them with enforcement policies. A marker is declarative: it does
not authorize a request or bypass the existing authentication/CSRF guards; the route
(or its service) still asks ``AuthzService`` with the legacy answer as fallback.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal, ParamSpec, TypeVar, get_args

from fastapi import FastAPI
from fastapi.routing import APIRoute

BareKind = Literal["LEGACY", "E2E_ONLY", "PUBLIC", "SELF", "CATALOG", "SUPER", "GOVERNANCE"]
PolicyKind = BareKind | Literal["RES", "CAP", "PRIVATE", "KEY", "SHARE_TOKEN", "CANDIDATES"]
P = ParamSpec("P")
R = TypeVar("R")


@dataclass(frozen=True, slots=True)
class KindPolicy:
    """A policy without parameters (``PUBLIC``, ``SELF``, ``LEGACY`` ...)."""

    kind: BareKind

    def __post_init__(self) -> None:
        if self.kind not in get_args(BareKind):
            raise ValueError(f"unknown bare policy kind {self.kind!r}")


@dataclass(frozen=True, slots=True)
class ResourcePolicy:
    """``RES``: the path parameter's resource permission; ``min_visible`` splits 404/403."""

    type_: str
    permission: str
    param: str
    min_visible: str
    kind: Literal["RES"] = field(default="RES", init=False)


@dataclass(frozen=True, slots=True)
class CapabilityPolicy:
    """``CAP``: a current-organization capability (403 ``capability_required``)."""

    capability: str
    kind: Literal["CAP"] = field(default="CAP", init=False)


@dataclass(frozen=True, slots=True)
class PrivatePolicy:
    """``PRIVATE``: ``user_id = me AND org_id = current org`` on ``model`` (no OpenFGA)."""

    model: str
    param: str | None = None
    kind: Literal["PRIVATE"] = field(default="PRIVATE", init=False)


@dataclass(frozen=True, slots=True)
class KeyPolicy:
    """``KEY``: API key -> deployment -> agent ``permission``."""

    permission: str = "can_deploy"
    kind: Literal["KEY"] = field(default="KEY", init=False)


@dataclass(frozen=True, slots=True)
class ShareTokenPolicy:
    """``SHARE_TOKEN``: a public share link; the token is the only credential."""

    kind: Literal["SHARE_TOKEN"] = field(default="SHARE_TOKEN", init=False)


@dataclass(frozen=True, slots=True)
class CandidatesPolicy:
    """List page: DB candidates (``candidates.py``) confirmed by BatchCheck (plan 8.10)."""

    type_: str
    permission: str
    kind: Literal["CANDIDATES"] = field(default="CANDIDATES", init=False)


RoutePolicy = (
    KindPolicy
    | ResourcePolicy
    | CapabilityPolicy
    | PrivatePolicy
    | KeyPolicy
    | ShareTokenPolicy
    | CandidatesPolicy
)
_POLICY_TYPES: Final = (
    KindPolicy,
    ResourcePolicy,
    CapabilityPolicy,
    PrivatePolicy,
    KeyPolicy,
    ShareTokenPolicy,
    CandidatesPolicy,
)

LEGACY: Final = KindPolicy("LEGACY")
E2E_ONLY: Final = KindPolicy("E2E_ONLY")
PUBLIC: Final = KindPolicy("PUBLIC")
SELF: Final = KindPolicy("SELF")
CATALOG: Final = KindPolicy("CATALOG")
SUPER: Final = KindPolicy("SUPER")
GOVERNANCE: Final = KindPolicy("GOVERNANCE")


def RES(  # noqa: N802 - plan 8.3 notation
    type_: str, permission: str, *, param: str, min_visible: str
) -> ResourcePolicy:
    return ResourcePolicy(type_, permission, param, min_visible)


def CAP(cap: str) -> CapabilityPolicy:  # noqa: N802 - plan 8.3 notation
    return CapabilityPolicy(cap)


def PRIVATE(model: str, *, param: str | None = None) -> PrivatePolicy:  # noqa: N802
    return PrivatePolicy(model, param)


def KEY(permission: str = "can_deploy") -> KeyPolicy:  # noqa: N802 - plan 8.3 notation
    return KeyPolicy(permission)


def SHARE_TOKEN() -> ShareTokenPolicy:  # noqa: N802 - plan 8.3 notation
    return ShareTokenPolicy()


def CANDIDATES(type_: str, permission: str) -> CandidatesPolicy:  # noqa: N802 - plan 8.10
    return CandidatesPolicy(type_, permission)


def authz_policy(policy: RoutePolicy) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Attach a reviewed policy without wrapping or changing the signature."""
    if not isinstance(policy, _POLICY_TYPES):  # a bare string marker is a stale declaration
        raise TypeError(f"authz_policy needs a RoutePolicy, got {policy!r}")

    def declare(endpoint: Callable[P, R]) -> Callable[P, R]:
        endpoint.__authz_policy__ = policy
        return endpoint

    return declare


def policy_of(endpoint: object) -> RoutePolicy | None:
    """The declared policy of an endpoint, or ``None`` when it has none."""
    found = getattr(endpoint, "__authz_policy__", None)
    return found if isinstance(found, _POLICY_TYPES) else None


def policy_kind(endpoint: object) -> PolicyKind | None:
    """``policy_of(endpoint).kind``; what inventories and ratchets compare."""
    found = policy_of(endpoint)
    return None if found is None else found.kind


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
