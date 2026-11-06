"""Approval for runs nobody watches (feature plan v2 순번 13, 17.3).

A scheduled run, an Agent API call and their subagents have no conversation to show an
approval card in. Their ``interrupt_on`` policy is built as for a chat (tool risk,
company actions, guardrail approval rules), and then:

* ``wait`` (a schedule's default): the standard approval interrupt pauses the run; the
  request goes to the approval inbox and the run resumes once someone decides.
* ``block`` (Agent API, schedules that chose it): the call is not made; the agent gets
  ``{"status": "blocked", "reason", "tool", "required_approval": true}`` and can try
  another way or leave the task to a person. Every other call of the run goes on.

Children (declared subagents and the general-purpose one) follow the same mode with their
own policy, so delegating a call to a subagent never skips its approval.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final, Literal

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from app.tools.risk import default_deepagents_interrupt_policy

UnattendedMode = Literal["wait", "block"]
BLOCKED_REASON: Final = "approval_required_without_attendant"


def blocked_result(call: Mapping[str, Any]) -> ToolMessage:
    name = str(call.get("name") or "")
    body = {
        "status": "blocked",
        "reason": "이 도구는 사람의 승인이 필요하지만 이 실행에는 승인할 사람이 없어 "
        "실행하지 않았습니다. 다른 방법을 찾거나, 사람이 할 일로 남기세요.",
        "tool": name,
        "required_approval": True,
    }
    return ToolMessage(
        content=json.dumps(body, ensure_ascii=False),
        tool_call_id=str(call.get("id") or ""),
        name=name,
        status="error",
    )


class UnattendedApprovalMiddleware(AgentMiddleware):
    """Answers BLOCKED for every call its ``interrupt_on`` policy would ask about."""

    def __init__(self, policy: Mapping[str, Any]) -> None:
        super().__init__()
        self.policy = {name: entry for name, entry in policy.items() if entry}

    @property
    def name(self) -> str:  # type: ignore[override]
        return "MoldyUnattendedApprovalMiddleware"

    def _blocked(self, request: Any) -> ToolMessage | None:
        call = request.tool_call
        entry = self.policy.get(call.get("name"))
        if not entry:
            return None
        when = entry.get("when") if isinstance(entry, Mapping) else None
        if callable(when) and not when(request):
            return None
        return blocked_result(call)

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return self._blocked(request) or handler(request)

    async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        blocked = self._blocked(request)
        return blocked if blocked is not None else await handler(request)


def strip_builtin_defaults(policy: Mapping[str, Any] | None) -> dict[str, Any]:
    """Without Deep Agents' own file tools at their default approval (17.3).

    A scheduled run has always written its own files (reports) without asking; a company
    action or guardrail rule that asks for more (a ``when`` predicate, other decisions)
    stays.
    """
    defaults = default_deepagents_interrupt_policy()
    return {
        name: entry
        for name, entry in (policy or {}).items()
        if entry and name != "ask_user" and not (name in defaults and entry == defaults[name])
    }


def _declarative(spec: Mapping[str, Any]) -> bool:
    return "runnable" not in spec and "graph_id" not in spec


def apply_unattended(
    mode: UnattendedMode | None,
    *,
    interrupt_on: dict[str, Any] | bool | None,
    middleware: list[Any],
    subagents: list[dict[str, Any]],
) -> tuple[dict[str, Any] | bool | None, list[Any], list[dict[str, Any]]]:
    """``(interrupt_on, middleware, subagents)`` for the run's mode (``None``: unchanged)."""
    if mode is None:
        return interrupt_on, middleware, subagents
    root = strip_builtin_defaults(interrupt_on if isinstance(interrupt_on, dict) else None)
    children: list[dict[str, Any]] = []
    for spec in subagents:
        if not _declarative(spec):
            children.append(spec)
            continue
        raw = spec.get("interrupt_on", root)
        own = strip_builtin_defaults(raw if isinstance(raw, dict) else None)
        if mode == "wait":
            children.append({**spec, "interrupt_on": own or None})
            continue
        child = {k: v for k, v in spec.items() if k != "interrupt_on"}
        if own:
            child["middleware"] = [
                *(spec.get("middleware") or []),
                UnattendedApprovalMiddleware(own),
            ]
        children.append(child)
    if mode == "wait":
        return root or None, middleware, children
    if root:
        middleware = [*middleware, UnattendedApprovalMiddleware(root)]
    return None, middleware, children


__all__ = [
    "BLOCKED_REASON",
    "strip_builtin_defaults",
    "UnattendedApprovalMiddleware",
    "UnattendedMode",
    "apply_unattended",
    "blocked_result",
]
