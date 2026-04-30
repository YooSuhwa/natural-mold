"""Tools domain — runtime registry of :class:`ToolDefinition` instances.

Public surface:
- :data:`registry` — process-wide :class:`ToolRegistry` populated at import
  time by :mod:`app.tools.definitions`.
- :class:`ToolDefinition`, :class:`ToolRunContext` — domain dataclasses.
- :func:`run_tool` — high-level runner that resolves a ``Tool`` ORM row to a
  ``ToolRunContext`` and invokes the definition's runner coroutine.
"""

# Side-effect import — definitions register themselves at import time.
from app.tools import definitions as definitions  # noqa: F401, E402
from app.tools.domain import ToolDefinition, ToolRunContext
from app.tools.parameters import FieldDef, FieldKind
from app.tools.registry import ToolRegistry, registry
from app.tools.runner import run_tool

__all__ = [
    "FieldDef",
    "FieldKind",
    "ToolDefinition",
    "ToolRegistry",
    "ToolRunContext",
    "registry",
    "run_tool",
]
