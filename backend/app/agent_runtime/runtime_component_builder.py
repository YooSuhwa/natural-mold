"""Runtime component builder — legacy compatibility facade.

Implementation lives in ``runtime_agent_builder`` (graph build),
``runtime_subagent_normalization`` (subagent specs),
``runtime_agent_preparation`` (component/agent preparation) and
``runtime_preparation_bindings`` (per-call collaborators), plus the
``app.agent_runtime.runtime`` clusters (BE-S10).

Patch contract: tests patch attributes of this module after import and after
a prior call. The extracted modules resolve, at call time
(``runtime_facade_bindings.runtime_attr``), the names that were patch targets
when the split was made: graph construction (``create_deep_agent``,
``build_agent``, the filesystem/subagent compatibility helpers), preparation
collaborators captured by ``_runtime_preparation_bindings`` and
``_prepare_agent``'s collaborators. Other re-exported names are bound
directly in the implementation modules; route a new patch target through
``runtime_attr`` before relying on it.
"""

from __future__ import annotations

import logging
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.middleware.filesystem import FilesystemMiddleware, FilesystemPermission
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
from deepagents.middleware.summarization import create_summarization_middleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from app.agent_runtime.deep_agent_factory import (
    DeepAgentFactoryBindings as _DeepAgentFactoryBindings,
)
from app.agent_runtime.deep_agent_factory import (
    actor_backend_impl as _actor_backend_impl,
)
from app.agent_runtime.deep_agent_factory import (
    build_agent_impl as _build_agent_impl,
)
from app.agent_runtime.deep_agent_factory import (
    build_moldy_filesystem_middleware_impl as _build_moldy_filesystem_middleware_impl,
)
from app.agent_runtime.deep_agent_factory import (
    middleware_name_impl as _middleware_name_impl,
)
from app.agent_runtime.deep_agent_factory import (
    normalize_declarative_subagents_impl as _normalize_declarative_subagents_impl,
)
from app.agent_runtime.deep_agent_factory import (
    with_actor_summarization_impl as _with_actor_summarization_impl,
)
from app.agent_runtime.deep_agent_factory import (
    with_moldy_deepagents_compatibility_impl as _with_moldy_deepagents_compatibility_impl,
)
from app.agent_runtime.filesystem_permissions import (
    add_scoped_offload_permissions,
    build_filesystem_permissions,
)
from app.agent_runtime.mcp_tool_loader import _build_mcp_tools
from app.agent_runtime.message_utils import RuntimeMessage, convert_to_langchain_messages
from app.agent_runtime.middleware_registry import (
    DEEPAGENT_BUILTIN_TYPES,
    build_middleware_instances,
    get_provider_middleware,
)
from app.agent_runtime.model_factory import create_chat_model
from app.agent_runtime.offload_storage import GENERAL_PURPOSE_ACTOR, ScopedOffloadBackend
from app.agent_runtime.run_secrets import add_run_secrets, collect_secret_values
from app.agent_runtime.runtime.interrupts import (
    _build_interrupt_on_policy,
    _default_interrupt_on_from_tools,
)
from app.agent_runtime.runtime.memory_context import (
    _RECALLED_MEMORY_PREVIEW_CHARS,
    _load_memory_context,
    _memory_write_policy_for_run,
    _parse_uuid,
    _recalled_memory_briefs,
)
from app.agent_runtime.runtime.models import (
    _MIDDLEWARE_MODEL_FIELDS,
    MiddlewareModelCredentialRequiredError,
    _build_model_candidates,
    _build_model_with_fallback,
    _is_retryable_model_error,
    _model_chain,
    _model_constructor_params,
    _resolve_middleware_model_params,
)
from app.agent_runtime.runtime.prompts import (
    _artifact_file_instruction_prompt,
    _interactive_tool_instruction_prompt,
    _memory_tool_instruction_prompt,
    _system_prompt_with_temporal_context,
)
from app.agent_runtime.runtime.reliability import (
    EmptyContentRetryMiddleware,
    _build_default_reliability_middleware,
    _has_visible_ai_content,
)
from app.agent_runtime.runtime_agent_builder import (
    _ARTIFACT_WRITE_FILESYSTEM_TOOL_NAMES,
    _FILESYSTEM_MIDDLEWARE_NAME,
    _INSPECT_FILESYSTEM_TOOL_NAMES,
    _MOLDY_FILESYSTEM_TOOL_NAMES,
    _TODO_LIST_MIDDLEWARE_NAME,
    _build_moldy_filesystem_middleware,
    _middleware_name,
    _stored_filesystem_tool_names,
    _with_moldy_deepagents_compatibility,
    _with_stored_parent_policy,
    _with_stored_policy_compatibility,
    _with_stored_policy_summarization,
    build_agent,
)
from app.agent_runtime.runtime_agent_preparation import (
    _DB_FREE_ACTOR_ID,
    _TEMPORAL_BUILTIN_TOOL_KEYS,
    _add_skill_secrets_to_run,
    _append_e2e_scripted_search_tool,
    _append_e2e_ui_data_demo_tool,
    _append_temporal_tools,
    _configured_recursion_limit,
    _prepare_agent,
    _prepare_runtime_components,
    _prepare_skill_builder_components,
    _scoped_runtime_backend,
    _selected_skill_slugs,
)
from app.agent_runtime.runtime_config import _DATA_DIR, AgentConfig, RuntimeComponents
from app.agent_runtime.runtime_policy import ResolvedRuntimePolicy
from app.agent_runtime.runtime_policy_capabilities import (
    STORED_RESERVED_TOOL_NAMES,
    RestrictedSubagentSpecError,
    build_stored_filesystem_permissions,
    sanitize_child_filesystem_permissions,
)
from app.agent_runtime.runtime_policy_parent_permissions import (
    canonicalize_stored_parent_permissions,
)
from app.agent_runtime.runtime_preparation import (
    prepare_runtime_components_impl as _prepare_runtime_components_impl,
)
from app.agent_runtime.runtime_preparation_bindings import _runtime_preparation_bindings
from app.agent_runtime.runtime_preparation_skill_builder import (
    prepare_skill_builder_components_impl as _prepare_skill_builder_components_impl,
)
from app.agent_runtime.runtime_preparation_support import (
    RuntimePreparationBindings as _RuntimePreparationBindings,
)
from app.agent_runtime.runtime_preparation_support import (
    add_skill_secrets_to_run_impl as _add_skill_secrets_to_run_impl,
)
from app.agent_runtime.runtime_preparation_support import (
    append_builtin_tools_impl as _append_builtin_tools_impl,
)
from app.agent_runtime.runtime_preparation_support import (
    build_runtime_skills_prompt as _build_runtime_skills_prompt_impl,
)
from app.agent_runtime.runtime_preparation_support import (
    configured_recursion_limit_impl as _configured_recursion_limit_impl,
)
from app.agent_runtime.runtime_preparation_support import (
    runtime_db_session_factory as _runtime_db_session_factory_impl,
)
from app.agent_runtime.runtime_preparation_support import (
    scoped_runtime_backend_impl as _scoped_runtime_backend_impl,
)
from app.agent_runtime.runtime_preparation_support import (
    selected_skill_slugs_impl as _selected_skill_slugs_impl,
)
from app.agent_runtime.runtime_subagent_normalization import (
    _GENERAL_PURPOSE_SUBAGENT_NAME,
    _MOLDY_ACTOR_ID_KEY,
    _SUMMARIZATION_MIDDLEWARE_NAME,
    _actor_backend,
    _normalize_declarative_subagents,
    _normalize_stored_policy_subagents,
    _with_actor_summarization,
)
from app.agent_runtime.runtime_summarization_policy import create_stored_summarization_middleware
from app.agent_runtime.skill_builder.chat_prompt import load_skill_builder_prompt
from app.agent_runtime.skill_builder.tools import (
    SESSION_CONSENT_ELIGIBLE_TOOLS as _SESSION_CONSENT_ELIGIBLE_TOOLS,
)
from app.agent_runtime.skill_builder.tools import build_skill_builder_tools
from app.agent_runtime.skill_executor import _create_skill_execute_tool
from app.agent_runtime.skill_tool_dependencies import build_skill_dependency_tool_configs
from app.agent_runtime.tool_factory import create_builtin_tool, create_tool_for_runtime
from app.agent_runtime.tools.ask_user import ask_user as ask_user_tool
from app.agent_runtime.tools.memory import build_memory_tools
from app.config import settings
from app.marketplace.skill_runtime import build_skill_runtime_context, resolve_runtime_credentials

logger = logging.getLogger(__name__)

# Explicit re-export list: every name is a legacy import or patch target.
__all__ = ["Any", "AgentConfig", "BaseChatModel", "BaseTool", "DEEPAGENT_BUILTIN_TYPES"]
__all__ += ["EmptyContentRetryMiddleware", "FilesystemMiddleware", "FilesystemPermission"]
__all__ += ["GENERAL_PURPOSE_ACTOR", "GENERAL_PURPOSE_SUBAGENT", "ResolvedRuntimePolicy"]
__all__ += ["MiddlewareModelCredentialRequiredError", "RestrictedSubagentSpecError"]
__all__ += ["RuntimeComponents", "RuntimeMessage", "STORED_RESERVED_TOOL_NAMES"]
__all__ += ["ScopedOffloadBackend", "StateBackend", "_ARTIFACT_WRITE_FILESYSTEM_TOOL_NAMES"]
__all__ += ["_DATA_DIR", "_DB_FREE_ACTOR_ID", "_DeepAgentFactoryBindings"]
__all__ += ["_FILESYSTEM_MIDDLEWARE_NAME", "_GENERAL_PURPOSE_SUBAGENT_NAME"]
__all__ += ["_INSPECT_FILESYSTEM_TOOL_NAMES", "_MIDDLEWARE_MODEL_FIELDS", "_MOLDY_ACTOR_ID_KEY"]
__all__ += ["_MOLDY_FILESYSTEM_TOOL_NAMES", "_RECALLED_MEMORY_PREVIEW_CHARS"]
__all__ += ["_RuntimePreparationBindings", "_SESSION_CONSENT_ELIGIBLE_TOOLS"]
__all__ += ["_SUMMARIZATION_MIDDLEWARE_NAME", "_TEMPORAL_BUILTIN_TOOL_KEYS"]
__all__ += ["_TODO_LIST_MIDDLEWARE_NAME", "_actor_backend", "_actor_backend_impl"]
__all__ += ["_add_skill_secrets_to_run", "_add_skill_secrets_to_run_impl"]
__all__ += ["_append_builtin_tools_impl", "_append_e2e_scripted_search_tool"]
__all__ += ["_append_e2e_ui_data_demo_tool", "_append_temporal_tools"]
__all__ += ["_artifact_file_instruction_prompt", "_build_agent_impl"]
__all__ += ["_build_default_reliability_middleware", "_build_interrupt_on_policy"]
__all__ += ["_build_mcp_tools", "_build_model_candidates", "_build_model_with_fallback"]
__all__ += ["_build_moldy_filesystem_middleware", "_build_moldy_filesystem_middleware_impl"]
__all__ += ["_build_runtime_skills_prompt_impl", "_configured_recursion_limit"]
__all__ += ["_configured_recursion_limit_impl", "_create_skill_execute_tool"]
__all__ += ["_default_interrupt_on_from_tools", "_has_visible_ai_content"]
__all__ += ["_interactive_tool_instruction_prompt", "_is_retryable_model_error"]
__all__ += ["_load_memory_context", "_memory_tool_instruction_prompt"]
__all__ += ["_memory_write_policy_for_run", "_middleware_name", "_middleware_name_impl"]
__all__ += ["_model_chain", "_model_constructor_params", "_normalize_declarative_subagents"]
__all__ += ["_normalize_declarative_subagents_impl", "_normalize_stored_policy_subagents"]
__all__ += ["_parse_uuid", "_prepare_agent", "_prepare_runtime_components"]
__all__ += ["_prepare_runtime_components_impl", "_prepare_skill_builder_components"]
__all__ += ["_prepare_skill_builder_components_impl", "_recalled_memory_briefs"]
__all__ += ["_resolve_middleware_model_params", "_runtime_db_session_factory_impl"]
__all__ += ["_runtime_preparation_bindings", "_scoped_runtime_backend"]
__all__ += ["_scoped_runtime_backend_impl", "_selected_skill_slugs", "_selected_skill_slugs_impl"]
__all__ += ["_stored_filesystem_tool_names", "_system_prompt_with_temporal_context"]
__all__ += ["_with_actor_summarization", "_with_actor_summarization_impl"]
__all__ += ["_with_moldy_deepagents_compatibility", "_with_moldy_deepagents_compatibility_impl"]
__all__ += ["_with_stored_parent_policy", "_with_stored_policy_compatibility"]
__all__ += ["_with_stored_policy_summarization", "add_run_secrets"]
__all__ += ["add_scoped_offload_permissions", "ask_user_tool", "build_agent"]
__all__ += ["build_filesystem_permissions", "build_memory_tools", "build_middleware_instances"]
__all__ += ["build_skill_builder_tools", "build_skill_dependency_tool_configs"]
__all__ += ["build_skill_runtime_context", "build_stored_filesystem_permissions"]
__all__ += ["canonicalize_stored_parent_permissions", "collect_secret_values"]
__all__ += ["convert_to_langchain_messages", "create_builtin_tool", "create_chat_model"]
__all__ += ["create_deep_agent", "create_stored_summarization_middleware"]
__all__ += ["create_summarization_middleware", "create_tool_for_runtime"]
__all__ += ["get_provider_middleware", "load_skill_builder_prompt", "resolve_runtime_credentials"]
__all__ += ["sanitize_child_filesystem_permissions", "settings"]
