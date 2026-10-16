"""Closed middleware set accepted under a prepared lease (parent Task 10, internal I12).

The census rejects any middleware instance whose exact class is not listed
here (subclasses included): ``ToolRetryMiddleware`` would re-enter a leaf
outside a fresh permit, ``LLMToolEmulator`` performs an unleased provider send
in place of the tool, and an unknown class is unreviewed dispatch surface.

The list covers what the installed ``create_deep_agent`` stack adds by default
(skills, filesystem, subagents, summarization, patch-tool-calls, Anthropic
prompt caching, memory, HITL, tool exclusion), ``TodoListMiddleware`` and the
app's lease guards. App classes are referenced by qualname string so this
module imports no other slice.

Ordering: middleware listed after ``LeaseBoundToolMiddleware`` wraps tool calls
*inside* it (within the open permit). Such a position is accepted only for
``INNER_TOOL_WRAPPERS``; ``FilesystemMiddleware`` only evicts large tool results
into the validated lease backend.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware

LEASE_TOOL_GUARD = "app.agent_runtime.lease_bound_tool.LeaseBoundToolMiddleware"
PREPARED_TASK_DISPATCH = "app.agent_runtime.prepared_task_middleware.PreparedTaskDispatchMiddleware"
FILESYSTEM_MIDDLEWARE = "deepagents.middleware.filesystem.FilesystemMiddleware"

LEASE_MIDDLEWARE_ALLOWLIST: frozenset[str] = frozenset(
    {
        LEASE_TOOL_GUARD,
        PREPARED_TASK_DISPATCH,
        FILESYSTEM_MIDDLEWARE,
        "deepagents.middleware.skills.SkillsMiddleware",
        "deepagents.middleware.subagents.SubAgentMiddleware",
        "deepagents.middleware.summarization._DeepAgentsSummarizationMiddleware",
        "deepagents.middleware.patch_tool_calls.PatchToolCallsMiddleware",
        "deepagents.middleware.memory.MemoryMiddleware",
        "deepagents.middleware._tool_exclusion._ToolExclusionMiddleware",
        "langchain_anthropic.middleware.prompt_caching.AnthropicPromptCachingMiddleware",
        "langchain.agents.middleware.human_in_the_loop.HumanInTheLoopMiddleware",
        "langchain.agents.middleware.todo.TodoListMiddleware",
    }
)
INNER_TOOL_WRAPPERS: frozenset[str] = frozenset({FILESYSTEM_MIDDLEWARE})


def class_qualname(value: object) -> str:
    cls = type(value)
    return f"{cls.__module__}.{cls.__qualname__}"


def wraps_tool_calls(value: object) -> bool:
    cls = type(value)
    return (
        getattr(cls, "wrap_tool_call", None) is not AgentMiddleware.wrap_tool_call
        or getattr(cls, "awrap_tool_call", None) is not AgentMiddleware.awrap_tool_call
    )


def unsupported_middleware(middleware: Sequence[Any]) -> str | None:
    """Qualname of the first middleware not accepted under a lease, else ``None``."""

    names = [class_qualname(item) for item in middleware]
    for name in names:
        if name not in LEASE_MIDDLEWARE_ALLOWLIST:
            return name
    if names.count(LEASE_TOOL_GUARD) > 1:
        return LEASE_TOOL_GUARD
    if LEASE_TOOL_GUARD not in names:
        return None
    start = names.index(LEASE_TOOL_GUARD) + 1
    for item, name in zip(middleware[start:], names[start:], strict=True):
        if wraps_tool_calls(item) and name not in INNER_TOOL_WRAPPERS:
            return name
    return None
