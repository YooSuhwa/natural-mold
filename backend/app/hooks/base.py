"""Hook framework base contracts.

Cross-cutting concerns (audit logging, spend tracking, guardrails) are
expressed as pluggable :class:`CustomHook` instances that the runtime invokes
around every agent / tool / MCP / model call. Each hook receives a
:class:`HookContext` describing what is about to run (or just ran) plus a
:class:`HookResult` on success.

The pre/post/failure pattern is borrowed from prior art in the LiteLLM proxy
``CustomLogger`` interface (see ``NOTICES.md``); identifiers, fields, and
wire format are Moldy-native and we do not import or copy any external code.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

HookKind = Literal["agent_invoke", "tool_call", "mcp_call", "model_test"]


@dataclass
class HookContext:
    """Snapshot of an in-flight call. Mutated only by the dispatcher.

    ``request_id`` correlates pre/post/failure for the same call. ``metadata``
    is a free-form dict for hook-specific extras (tool name, MCP server URL,
    model id, etc.). All FK-style fields are optional because not every kind
    populates the full set (a generic tool call has no ``model_id``).
    """

    request_id: str
    kind: HookKind
    user_id: uuid.UUID
    started_at: datetime
    agent_id: uuid.UUID | None = None
    tool_id: uuid.UUID | None = None
    mcp_server_id: uuid.UUID | None = None
    model_id: uuid.UUID | None = None
    credential_id: uuid.UUID | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HookResult:
    """Post-call outcome summary. ``output`` is intentionally truncated by
    callers (typically <= 200 chars) so audit rows stay small.
    """

    duration_ms: int
    output: Any | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None


class CustomHook:
    """Pluggable cross-cutting hook.

    Subclasses override the hooks they care about; the dispatcher
    (:class:`app.hooks.registry.HookRegistry`) calls all three lifecycle
    methods in order. Each method has a default no-op implementation so
    subclasses only override what they need — the base class is concrete
    (not abstract) so a stub instance is a useful no-op for tests.
    """

    name: str = "custom_hook"
    enabled: bool = True

    async def async_pre_call_hook(self, ctx: HookContext) -> None:
        """Run before the dispatcher invokes the underlying call."""

    async def async_post_call_hook(self, ctx: HookContext, result: HookResult) -> None:
        """Run after a successful call returns. ``result`` carries timings."""

    async def async_failure_hook(self, ctx: HookContext, error: Exception) -> None:
        """Run when the underlying call raises. Mirror of post-call hook."""


__all__ = [
    "CustomHook",
    "HookContext",
    "HookKind",
    "HookResult",
]
