"""Shared types of the authorization service (plan 8.2, 8.5)."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID

from app.authz.client import CheckQuery, Consistency, ListQuery
from app.authz.model_document import model_document
from app.authz.principal import Principal

AuthzMode = Literal["legacy", "shadow", "enforce"]


class AuthzClient(Protocol):
    """Shared by the SDK adapter (A-03) and the fake (A-06)."""

    async def check(
        self, query: CheckQuery, *, consistency: Consistency = "MINIMIZE_LATENCY"
    ) -> bool: ...

    async def batch_check(
        self, queries: Sequence[CheckQuery], *, consistency: Consistency = "MINIMIZE_LATENCY"
    ) -> tuple[bool, ...]: ...

    async def list_objects(self, query: ListQuery) -> tuple[str, ...]: ...


class Verdict(StrEnum):
    ALLOW = "allow"
    NOT_FOUND = "not_found"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True, slots=True)
class ShadowSample:
    """Everything the shadow recorder needs to re-decide off the request path."""

    principal: Principal
    permission: str
    obj: str
    min_visible: str | None
    resource_org_id: UUID | None
    legacy: Verdict


ShadowHook = Callable[[ShadowSample], None]


class AuthzNotConsulted(RuntimeError):
    """OpenFGA was asked for an answer while the installation runs in legacy mode."""


def known_resource_types() -> frozenset[str]:
    """Object types of the canonical model; ``user`` is only ever a subject."""
    names = (definition["type"] for definition in model_document().type_definitions)
    return frozenset(str(name) for name in names) - {"user"}


def parse_enforce_types(raw: str) -> frozenset[str]:
    """Comma-separated AUTHZ_ENFORCE_TYPES; unknown types are a configuration error."""
    types = frozenset(t.strip() for t in raw.split(",") if t.strip())
    if unknown := types - known_resource_types():
        raise ValueError(f"unknown AUTHZ_ENFORCE_TYPES: {', '.join(sorted(unknown))}")
    return types
