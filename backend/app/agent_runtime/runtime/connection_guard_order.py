"""Stack position of ``ConnectionGuardMiddleware``: before HITL approval (plan 11.4; F-07).

LangChain runs ``after_model`` hooks in reverse list order, and Deep Agents appends its
``HumanInTheLoopMiddleware`` to the tail of the final stack (user middleware lands ahead
of the tail). To check connections *before* anything is offered for approval, the
interactive guard takes the HITL slot by name (Deep Agents replaces a same-named entry
in place, so the guard is the last ``after_model`` entry and runs first) and the
approval step moves into the app slice as ``GuardedApprovalMiddleware`` with the same
merged policy Deep Agents would have built (``interrupt_on`` + filesystem permission
interrupts). Calls the guard already answered are never offered for approval, so an
approval can never add a second ToolMessage for the same call.

Guard and approval stay separate graph nodes: each node's ``interrupt()`` sequence is
then deterministic on resume. Subagent graphs keep Deep Agents' own HITL placement
(their guard only refuses; see ``connection_guard``).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from deepagents.graph import _merge_fs_interrupt_on
from deepagents.middleware._fs_interrupt import _build_interrupt_on_from_permissions
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain.agents.middleware.human_in_the_loop import InterruptOnConfig
from langchain_core.messages import ToolMessage
from langchain_core.messages.tool import ToolCall

from app.agent_runtime.runtime.connection_guard import ConnectionGuardMiddleware

HITL_SLOT = "HumanInTheLoopMiddleware"
APPROVAL_NAME = "GuardedApprovalMiddleware"


class GuardedApprovalMiddleware(HumanInTheLoopMiddleware):
    """Deep Agents' approval step, run after ``ConnectionGuardMiddleware``."""

    @property
    def name(self) -> str:
        return APPROVAL_NAME

    def _should_interrupt(
        self, tool_call: ToolCall, config: InterruptOnConfig, state: Any, runtime: Any
    ) -> bool:
        answered = {
            message.tool_call_id
            for message in state.get("messages", ())
            if isinstance(message, ToolMessage)
        }
        if tool_call.get("id") in answered:
            return False
        return super()._should_interrupt(tool_call, config, state, runtime)


def order_connection_guard[T](
    middleware: T,
    *,
    interrupt_on: dict[str, Any] | bool | None,
    permissions: Sequence[Any] | None,
) -> T | list[Any]:
    """``middleware`` with the interactive guard placed ahead of the approval step.

    Without an interactive guard the very same object is returned (no copy).
    """

    if not isinstance(middleware, list | tuple):
        return middleware
    guard = next((m for m in middleware if isinstance(m, ConnectionGuardMiddleware)), None)
    if guard is None or not guard.policy.interactive:
        return middleware
    explicit = interrupt_on if isinstance(interrupt_on, dict) else None
    merged = _merge_fs_interrupt_on(
        _build_interrupt_on_from_permissions(list(permissions or [])), explicit
    )
    if merged is None:
        return middleware  # no approval step: the guard keeps its own slot
    guard.take_slot(HITL_SLOT)
    rest = [m for m in middleware if m is not guard]
    return [*rest, GuardedApprovalMiddleware(interrupt_on=merged), guard]


__all__ = ["APPROVAL_NAME", "GuardedApprovalMiddleware", "HITL_SLOT", "order_connection_guard"]
