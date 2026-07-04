"""Compatibility facade for Moldy agent runtime execution.

Implementation lives in focused modules:

* runtime_config
* skill_executor
* mcp_tool_loader
* runtime_component_builder
* agent_stream_runner
"""

from __future__ import annotations

from app.agent_runtime.agent_stream_runner import (
    _hook_ctx_for_agent,
    _hook_result_from_usage,
    _run_agent_stream,
    execute_agent_invoke,
    execute_agent_stream,
    resume_agent_stream,
)
from app.agent_runtime.langgraph_agent_stream_runner import (
    execute_agent_stream_langgraph,
    resume_agent_stream_langgraph,
)
from app.agent_runtime.mcp_tool_loader import _build_mcp_tools, _create_mcp_error_stub
from app.agent_runtime.runtime_component_builder import (
    EmptyContentRetryMiddleware,
    MiddlewareModelCredentialRequiredError,
    _build_default_reliability_middleware,
    _build_model_candidates,
    _build_model_with_fallback,
    _configured_recursion_limit,
    _load_memory_context,
    _memory_write_policy_for_run,
    _prepare_agent,
    _prepare_runtime_components,
    _resolve_middleware_model_params,
    build_agent,
)
from app.agent_runtime.runtime_config import _DATA_DIR, AgentConfig, RuntimeComponents
from app.agent_runtime.skill_executor import (
    _create_skill_execute_tool,
    _expand_shell_vars,
    _prepare_skill_subprocess_args,
    _skill_timeout_seconds,
)

__all__ = [
    "AgentConfig",
    "RuntimeComponents",
    "_DATA_DIR",
    "build_agent",
    "MiddlewareModelCredentialRequiredError",
    "EmptyContentRetryMiddleware",
    "_build_default_reliability_middleware",
    "_build_model_candidates",
    "_build_model_with_fallback",
    "_configured_recursion_limit",
    "_load_memory_context",
    "_memory_write_policy_for_run",
    "_prepare_agent",
    "_prepare_runtime_components",
    "_resolve_middleware_model_params",
    "_create_skill_execute_tool",
    "_expand_shell_vars",
    "_prepare_skill_subprocess_args",
    "_skill_timeout_seconds",
    "_build_mcp_tools",
    "_create_mcp_error_stub",
    "_hook_ctx_for_agent",
    "_hook_result_from_usage",
    "_run_agent_stream",
    "execute_agent_stream",
    "execute_agent_stream_langgraph",
    "resume_agent_stream_langgraph",
    "resume_agent_stream",
    "execute_agent_invoke",
]
