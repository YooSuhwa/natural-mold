"""HiTL ``interrupt_on`` 정책 조립 (BE-S10 분리).

CLAUDE.md 의 HiTL 기본 인터럽트 규칙이 참조하는
``_default_interrupt_on_from_tools`` 가 이 모듈에 있다 — 위험 메타데이터가
있는 도구는 별도 미들웨어 설정 없이 기본 ``interrupt_on`` 정책이 붙는다.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool

from app.tools.risk import (
    default_deepagents_interrupt_policy,
    interrupt_policy_for_tool,
    merge_interrupt_policies,
)


def _default_interrupt_on_from_tools(tools: list[BaseTool]) -> dict[str, Any]:
    """Build the minimum HITL policy from attached tool risk metadata."""

    policy = default_deepagents_interrupt_policy()
    for tool in tools:
        policy.update(interrupt_policy_for_tool(tool))
    return policy


def _build_interrupt_on_policy(
    middleware_configs: list[dict[str, Any]] | None,
    tools: list[BaseTool],
    *,
    include_ask_user: bool,
    is_trigger_mode: bool,
) -> dict[str, Any] | None:
    """Build the DeepAgents top-level ``interrupt_on`` policy.

    DeepAgents propagates top-level HITL policy to its built-in subagent
    middleware. Keep the policy out of the explicit middleware list so
    ``ask_user`` and delegated tool calls share the same standard path.
    """

    if is_trigger_mode:
        return None

    interrupt_on: dict[str, Any] = _default_interrupt_on_from_tools(tools)
    for mw_config in middleware_configs or []:
        if mw_config.get("type") != "human_in_the_loop":
            continue
        explicit = mw_config.get("params", {}).get("interrupt_on")
        if isinstance(explicit, dict):
            interrupt_on = merge_interrupt_policies(interrupt_on, explicit)
        break

    policy = dict(interrupt_on or {})
    if include_ask_user:
        policy.setdefault("ask_user", {"allowed_decisions": ["respond"]})
    return policy or None
