"""Read-time child identities approved for public chat projection."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChildVisibility:
    allow_legacy_details: bool
    tool_call_ids: frozenset[str]
    namespaces: frozenset[tuple[str, ...]]
    # A stable call, exact checkpoint namespace and durable task ID proved together.
    bindings: frozenset[tuple[str, tuple[str, ...], str]] = frozenset()
    # Historical native delegations may prove the issuer and namespace without
    # a durable task ID. They authorize reading only.
    historical_bindings: frozenset[tuple[str, tuple[str, ...]]] = frozenset()
