"""Process-wide credential definition registry.

Definitions register themselves at import time via :data:`registry`. Lookup is
case-sensitive on the definition ``key``.
"""

from __future__ import annotations

from collections import defaultdict

from app.credentials.domain import CredentialDefinition


class CredentialRegistry:
    """In-memory registry of :class:`CredentialDefinition` instances."""

    def __init__(self) -> None:
        self._items: dict[str, CredentialDefinition] = {}

    def register(self, definition: CredentialDefinition) -> CredentialDefinition:
        if definition.key in self._items:
            # Re-registration of the same instance is a no-op (idempotent imports);
            # registering a *different* definition under the same key is a bug.
            existing = self._items[definition.key]
            if existing is not definition:
                raise ValueError(
                    f"credential definition '{definition.key}' already registered"
                )
            return existing
        self._items[definition.key] = definition
        return definition

    def get(self, key: str) -> CredentialDefinition | None:
        return self._items.get(key)

    def require(self, key: str) -> CredentialDefinition:
        definition = self._items.get(key)
        if definition is None:
            raise KeyError(f"unknown credential definition '{key}'")
        return definition

    def all(self) -> list[CredentialDefinition]:
        return list(self._items.values())

    def by_category(self) -> dict[str, list[CredentialDefinition]]:
        out: dict[str, list[CredentialDefinition]] = defaultdict(list)
        for definition in self._items.values():
            out[definition.category].append(definition)
        return dict(out)

    def clear(self) -> None:
        """Drop all registrations — used by tests that re-import definitions."""

        self._items.clear()


# Process singleton — populated by ``app.credentials.definitions`` import.
registry = CredentialRegistry()

__all__ = ["CredentialRegistry", "registry"]
