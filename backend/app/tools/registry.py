"""Process-wide tool definition registry."""

from __future__ import annotations

from collections import defaultdict

from app.tools.domain import ToolDefinition


class ToolRegistry:
    """In-memory registry of :class:`ToolDefinition` instances."""

    def __init__(self) -> None:
        self._items: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> ToolDefinition:
        existing = self._items.get(definition.key)
        if existing is not None:
            if existing is definition:
                return existing
            raise ValueError(
                f"tool definition '{definition.key}' already registered"
            )
        self._items[definition.key] = definition
        return definition

    def get(self, key: str) -> ToolDefinition | None:
        return self._items.get(key)

    def require(self, key: str) -> ToolDefinition:
        definition = self._items.get(key)
        if definition is None:
            raise KeyError(f"unknown tool definition '{key}'")
        return definition

    def all(self) -> list[ToolDefinition]:
        return list(self._items.values())

    def by_category(self) -> dict[str, list[ToolDefinition]]:
        out: dict[str, list[ToolDefinition]] = defaultdict(list)
        for definition in self._items.values():
            out[definition.category].append(definition)
        return dict(out)

    def clear(self) -> None:
        """Drop all registrations — used by tests that re-import definitions."""

        self._items.clear()


registry = ToolRegistry()

__all__ = ["ToolRegistry", "registry"]
