"""Read-time child identities approved for public chat projection."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChildVisibility:
    allow_legacy_details: bool
    tool_call_ids: frozenset[str]
    namespaces: frozenset[tuple[str, ...]]
