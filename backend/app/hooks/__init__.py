"""Hook framework public surface.

Re-exports the base contracts plus the module-level singleton and a
``register_default_hooks`` helper called from the FastAPI lifespan.
"""

from __future__ import annotations

import logging

from app.hooks.base import CustomHook, HookContext, HookKind, HookResult
from app.hooks.builtin import AuditHook, LoggingHook, SpendHook
from app.hooks.registry import HookRegistry, hooks

logger = logging.getLogger(__name__)


def register_default_hooks() -> None:
    """Register the built-in hook set on the module-global registry.

    Safe to call multiple times — :meth:`HookRegistry.register` replaces by
    name, so re-running during startup or tests yields a stable order without
    duplicates.
    """

    hooks.register(LoggingHook())
    hooks.register(AuditHook())
    hooks.register(SpendHook())
    logger.info("registered default hooks: %s", [h.name for h in hooks.all()])


__all__ = [
    "AuditHook",
    "CustomHook",
    "HookContext",
    "HookKind",
    "HookRegistry",
    "HookResult",
    "LoggingHook",
    "SpendHook",
    "hooks",
    "register_default_hooks",
]
