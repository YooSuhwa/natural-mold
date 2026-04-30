"""Singleton :class:`HookRegistry` and dispatcher.

The runtime imports the module-level ``hooks`` instance and calls
``run_pre`` / ``run_post`` / ``run_failure`` around the work it dispatches.
Each registered :class:`CustomHook` runs in registration order; a hook that
raises is logged at ``warning`` level and skipped — one bad hook can never
stall the dispatcher or another hook (failure isolation).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.hooks.base import CustomHook, HookContext, HookResult

logger = logging.getLogger(__name__)


class HookRegistry:
    """Ordered, in-process collection of :class:`CustomHook` instances.

    Registration is idempotent on ``hook.name`` — re-registering the same
    name replaces the existing instance (so re-importing ``builtin/`` during
    tests doesn't pile up duplicates).
    """

    def __init__(self) -> None:
        self._hooks: list[CustomHook] = []

    # -- Registration --------------------------------------------------------

    def register(self, hook: CustomHook) -> None:
        existing = {h.name: idx for idx, h in enumerate(self._hooks)}
        if hook.name in existing:
            self._hooks[existing[hook.name]] = hook
            return
        self._hooks.append(hook)

    def all(self) -> list[CustomHook]:
        return list(self._hooks)

    def clear(self) -> None:
        """Drop every registered hook. Used by tests for isolation."""

        self._hooks.clear()

    # -- Dispatch ------------------------------------------------------------

    async def run_pre(self, ctx: HookContext) -> None:
        for hook in self._hooks:
            if not hook.enabled:
                continue
            try:
                await hook.async_pre_call_hook(ctx)
            except Exception:  # noqa: BLE001 — failure isolation
                logger.warning(
                    "pre-call hook %s raised; continuing", hook.name, exc_info=True
                )

    async def run_post(self, ctx: HookContext, result: HookResult) -> None:
        for hook in self._hooks:
            if not hook.enabled:
                continue
            try:
                await hook.async_post_call_hook(ctx, result)
            except Exception:  # noqa: BLE001 — failure isolation
                logger.warning(
                    "post-call hook %s raised; continuing", hook.name, exc_info=True
                )

    async def run_failure(self, ctx: HookContext, error: Exception) -> None:
        for hook in self._hooks:
            if not hook.enabled:
                continue
            try:
                await hook.async_failure_hook(ctx, error)
            except Exception:  # noqa: BLE001 — failure isolation
                logger.warning(
                    "failure hook %s raised; continuing", hook.name, exc_info=True
                )


# Module-global singleton — imported by the runtime + builtin hooks.
hooks = HookRegistry()


__all__ = ["HookRegistry", "hooks"]
