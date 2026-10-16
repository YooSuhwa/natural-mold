"""Call-time lookups of the legacy ``runtime_component_builder`` facade.

Tests and callers patch facade attributes after import and after a prior
call, so extracted modules resolve those collaborators through the facade at
call time instead of binding them at import.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any


def runtime_facade() -> ModuleType:
    """Return the live ``runtime_component_builder`` compatibility module."""

    from app.agent_runtime import runtime_component_builder

    return runtime_component_builder


def runtime_attr(name: str) -> Any:
    """Return ``runtime_component_builder.<name>`` as currently bound."""

    return getattr(runtime_facade(), name)
