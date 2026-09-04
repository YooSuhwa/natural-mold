"""Runtime component builder — 에이전트 실행 컴포넌트 조립 오케스트레이터.

This module is intentionally a narrow legacy compatibility facade. Its import
and monkeypatch surface is larger than the leaf-module size target, while new
implementation belongs in the extracted factory and preparation modules.

BE-S10: 모델 후보/폴백(``runtime.models``), 신뢰성 미들웨어
(``runtime.reliability``), HiTL 인터럽트 정책(``runtime.interrupts``),
프롬프트 블록(``runtime.prompts``), 장기 기억 컨텍스트
(``runtime.memory_context``) 클러스터는 ``app.agent_runtime.runtime``
패키지로 분리됐고, 이 모듈이 기존 표면을 그대로 재-export 한다.

Patch-contract notes (tests/test_executor.py, tests/test_model_fallback.py,
tests/test_hitl_middleware.py, tests/test_skill_builder_*.py):

- ``create_chat_model`` 은 이 모듈 attribute 로 남아야 한다 — 테스트가
  ``runtime_component_builder.create_chat_model`` 을 patch 하고,
  ``runtime.models`` 의 함수들이 call-time 에 이 모듈을 경유해 조회한다.
- ``_build_model_candidates`` / ``_load_memory_context`` /
  ``_memory_write_policy_for_run`` 은 이 모듈 binding 을 patch 하는 테스트가
  있으므로, 잔류 함수들은 모듈 global(재-export binding)로 호출한다.
"""

from __future__ import annotations

import logging
import time
import uuid
from functools import partial
from pathlib import Path
from typing import Any, assert_never

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
from app.agent_runtime.message_utils import convert_to_langchain_messages
from app.agent_runtime.middleware_registry import (
    DEEPAGENT_BUILTIN_TYPES,
    build_middleware_instances,
    get_provider_middleware,
)
from app.agent_runtime.model_factory import create_chat_model as create_chat_model
from app.agent_runtime.offload_storage import (
    GENERAL_PURPOSE_ACTOR,
    ScopedOffloadBackend,
)
from app.agent_runtime.run_secrets import add_run_secrets, collect_secret_values
from app.agent_runtime.runtime.interrupts import _build_interrupt_on_policy
from app.agent_runtime.runtime.interrupts import (
    _default_interrupt_on_from_tools as _default_interrupt_on_from_tools,
)
from app.agent_runtime.runtime.memory_context import (
    _RECALLED_MEMORY_PREVIEW_CHARS as _RECALLED_MEMORY_PREVIEW_CHARS,
)
from app.agent_runtime.runtime.memory_context import (
    _load_memory_context,
    _memory_write_policy_for_run,
)
from app.agent_runtime.runtime.memory_context import (
    _parse_uuid as _parse_uuid,
)
from app.agent_runtime.runtime.memory_context import (
    _recalled_memory_briefs as _recalled_memory_briefs,
)
from app.agent_runtime.runtime.models import (
    _MIDDLEWARE_MODEL_FIELDS as _MIDDLEWARE_MODEL_FIELDS,
)
from app.agent_runtime.runtime.models import (
    MiddlewareModelCredentialRequiredError as MiddlewareModelCredentialRequiredError,
)
from app.agent_runtime.runtime.models import (
    _build_model_candidates,
    _resolve_middleware_model_params,
)
from app.agent_runtime.runtime.models import (
    _build_model_with_fallback as _build_model_with_fallback,
)
from app.agent_runtime.runtime.models import (
    _is_retryable_model_error as _is_retryable_model_error,
)
from app.agent_runtime.runtime.models import (
    _model_chain as _model_chain,
)
from app.agent_runtime.runtime.models import (
    _model_constructor_params as _model_constructor_params,
)
from app.agent_runtime.runtime.prompts import (
    _artifact_file_instruction_prompt,
    _interactive_tool_instruction_prompt,
    _memory_tool_instruction_prompt,
    _system_prompt_with_temporal_context,
)
from app.agent_runtime.runtime.reliability import (
    EmptyContentRetryMiddleware as EmptyContentRetryMiddleware,
)
from app.agent_runtime.runtime.reliability import (
    _build_default_reliability_middleware,
)
from app.agent_runtime.runtime.reliability import (
    _has_visible_ai_content as _has_visible_ai_content,
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
from app.agent_runtime.skill_builder.chat_prompt import load_skill_builder_prompt
from app.agent_runtime.skill_builder.tools import (
    SESSION_CONSENT_ELIGIBLE_TOOLS as _SESSION_CONSENT_ELIGIBLE_TOOLS,
)
from app.agent_runtime.skill_builder.tools import (
    build_skill_builder_tools,
)
from app.agent_runtime.skill_executor import _create_skill_execute_tool
from app.agent_runtime.skill_tool_dependencies import build_skill_dependency_tool_configs
from app.agent_runtime.tool_factory import create_builtin_tool, create_tool_for_runtime
from app.agent_runtime.tools.ask_user import ask_user as ask_user_tool
from app.agent_runtime.tools.memory import build_memory_tools
from app.config import settings
from app.marketplace.skill_runtime import build_skill_runtime_context, resolve_runtime_credentials

logger = logging.getLogger(__name__)

_TEMPORAL_BUILTIN_TOOL_KEYS = (
    "builtin:current_datetime",
    "builtin:resolve_relative_date",
)

# Deep Agents 0.7 makes ``delete`` part of its default filesystem surface and
# stops installing TodoListMiddleware. Moldy intentionally preserves its
# pre-0.7 non-deleting filesystem capability set and todo-state stream
# contract here, at the single build boundary used by chat, Assistant, and
# Skill Builder.
_MOLDY_FILESYSTEM_TOOL_NAMES = (
    "ls",
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
    "execute",
)
_INSPECT_FILESYSTEM_TOOL_NAMES = ("ls", "read_file", "glob", "grep")
_ARTIFACT_WRITE_FILESYSTEM_TOOL_NAMES = (
    "ls",
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
)
_FILESYSTEM_MIDDLEWARE_NAME = "FilesystemMiddleware"
_SUMMARIZATION_MIDDLEWARE_NAME = "SummarizationMiddleware"
_TODO_LIST_MIDDLEWARE_NAME = "TodoListMiddleware"
_GENERAL_PURPOSE_SUBAGENT_NAME = GENERAL_PURPOSE_SUBAGENT["name"]
_MOLDY_ACTOR_ID_KEY = "_moldy_actor_id"
_DB_FREE_ACTOR_ID = uuid.UUID("00000000-0000-4000-8000-000000000000")


def _middleware_name(middleware: Any) -> str | None:
    """Return a middleware's public name without assuming a concrete type."""

    return _middleware_name_impl(middleware)


def _build_moldy_filesystem_middleware(
    *,
    backend: Any,
    permissions: list[FilesystemPermission] | None,
) -> FilesystemMiddleware:
    """Build Moldy's deliberately non-deleting Deep Agents filesystem layer."""

    return _build_moldy_filesystem_middleware_impl(
        backend=backend,
        permissions=permissions,
        filesystem_tool_names=_MOLDY_FILESYSTEM_TOOL_NAMES,
        add_scoped_permissions=add_scoped_offload_permissions,
    )


def _with_moldy_deepagents_compatibility(
    middleware: list[Any] | tuple[Any, ...] | None,
    *,
    backend: Any,
    permissions: list[FilesystemPermission] | None,
) -> list[Any]:
    """Replace Deep Agents 0.7 default FS/todo behavior with Moldy's contract.

    The two compatibility middleware entries are intentionally recreated rather
    than reusing caller instances: the filesystem instance must share the same
    backend and effective permission list as the graph it configures.  All
    unrelated middleware retain their caller-provided relative order.
    """

    return _with_moldy_deepagents_compatibility_impl(
        middleware,
        backend=backend,
        permissions=permissions,
        middleware_name=_middleware_name,
        build_filesystem_middleware=_build_moldy_filesystem_middleware,
        filesystem_middleware_name=_FILESYSTEM_MIDDLEWARE_NAME,
        todo_list_middleware_name=_TODO_LIST_MIDDLEWARE_NAME,
    )


def _stored_filesystem_tool_names(
    runtime_policy: ResolvedRuntimePolicy,
) -> tuple[str, ...]:
    match runtime_policy.effective.filesystem.mode:
        case "inspect":
            return _INSPECT_FILESYSTEM_TOOL_NAMES
        case "artifact_write":
            return _ARTIFACT_WRITE_FILESYSTEM_TOOL_NAMES
        case unreachable:
            assert_never(unreachable)


def _with_stored_policy_compatibility(
    middleware: list[Any] | tuple[Any, ...] | None,
    *,
    backend: Any,
    permissions: list[FilesystemPermission] | None,
    filesystem_tool_names: tuple[str, ...],
    todo_enabled: bool,
) -> list[Any]:
    def build_filesystem_middleware(
        *,
        backend: Any,
        permissions: list[FilesystemPermission] | None,
    ) -> FilesystemMiddleware:
        return _build_moldy_filesystem_middleware_impl(
            backend=backend,
            permissions=permissions,
            filesystem_tool_names=filesystem_tool_names,
            add_scoped_permissions=add_scoped_offload_permissions,
        )

    return _with_moldy_deepagents_compatibility_impl(
        middleware,
        backend=backend,
        permissions=permissions,
        middleware_name=_middleware_name,
        build_filesystem_middleware=build_filesystem_middleware,
        filesystem_middleware_name=_FILESYSTEM_MIDDLEWARE_NAME,
        todo_list_middleware_name=_TODO_LIST_MIDDLEWARE_NAME,
        include_todo=todo_enabled,
    )


def _actor_backend(backend: Any, actor_id: uuid.UUID | str | None) -> Any:
    """Return an actor-scoped backend when Moldy's scoped backend is active."""

    return _actor_backend_impl(backend, actor_id)


def _with_actor_summarization(
    middleware: list[Any],
    *,
    model: BaseChatModel,
    backend: Any,
) -> list[Any]:
    """Replace Deep Agents' child summarizer with one using the child's backend."""

    return _with_actor_summarization_impl(
        middleware,
        model=model,
        backend=backend,
        middleware_name=_middleware_name,
        create_summarization_middleware=create_summarization_middleware,
        summarization_middleware_name=_SUMMARIZATION_MIDDLEWARE_NAME,
    )


def _scoped_runtime_backend(
    cfg: AgentConfig,
    *,
    run_id: str | None,
) -> ScopedOffloadBackend:
    """Build the trusted owner/conversation/run/actor storage boundary."""
    return _scoped_runtime_backend_impl(
        cfg,
        run_id=run_id,
        data_dir=_DATA_DIR,
        db_free_actor_id=_DB_FREE_ACTOR_ID,
    )


def _normalize_declarative_subagents(
    subagents: list[dict[str, Any]] | None,
    *,
    model: BaseChatModel,
    tools: list[BaseTool],
    backend: Any,
    permissions: list[FilesystemPermission] | None,
    interrupt_on: dict[str, Any] | bool | None,
    skills: list[str] | None,
) -> list[dict[str, Any]]:
    """Return copied declarative specs with Moldy's 0.7 compatibility layer.

    Deep Agents treats specs with ``runnable`` or ``graph_id`` as precompiled
    or asynchronous respectively.  Those are opaque caller-owned execution
    units, so this function leaves them untouched.  Declarative specs are
    copied before normalizing their effective middleware and inherited fields.
    """

    return _normalize_declarative_subagents_impl(
        subagents,
        model=model,
        tools=tools,
        backend=backend,
        permissions=permissions,
        interrupt_on=interrupt_on,
        skills=skills,
        actor_backend=_actor_backend,
        with_compatibility=_with_moldy_deepagents_compatibility,
        with_actor_summarization=_with_actor_summarization,
        general_purpose_subagent=GENERAL_PURPOSE_SUBAGENT,
        general_purpose_subagent_name=_GENERAL_PURPOSE_SUBAGENT_NAME,
        actor_id_key=_MOLDY_ACTOR_ID_KEY,
        general_purpose_actor=GENERAL_PURPOSE_ACTOR,
    )


def _normalize_stored_policy_subagents(
    subagents: list[dict[str, Any]] | None,
    *,
    model: BaseChatModel,
    tools: list[BaseTool],
    backend: Any,
    permissions: list[FilesystemPermission] | None,
    interrupt_on: dict[str, Any] | bool | None,
    skills: list[str] | None,
    filesystem_tool_names: tuple[str, ...],
    todo_enabled: bool,
) -> list[dict[str, Any]]:
    compatibility = partial(
        _with_stored_policy_compatibility,
        filesystem_tool_names=filesystem_tool_names,
        todo_enabled=todo_enabled,
    )
    allow_child_writes = "write_file" in filesystem_tool_names
    parent_child_permissions = sanitize_child_filesystem_permissions(
        permissions,
        permissions,
        child_name=None,
        trusted_child=False,
        allow_child_writes=allow_child_writes,
    )
    restricted_subagents: list[dict[str, Any]] = []
    for spec in subagents or ():
        if "runnable" in spec or "graph_id" in spec:
            raise RestrictedSubagentSpecError
        child = dict(spec)
        trusted_child = _MOLDY_ACTOR_ID_KEY in spec
        candidate_permissions = (
            spec.get("permissions", permissions) if trusted_child else permissions
        )
        child_name = spec.get("name")
        child["permissions"] = sanitize_child_filesystem_permissions(
            permissions,
            candidate_permissions,
            child_name=child_name if isinstance(child_name, str) else None,
            trusted_child=trusted_child,
            allow_child_writes=allow_child_writes,
        )
        child_tools = spec.get("tools")
        if isinstance(child_tools, (list, tuple)):
            child["tools"] = [
                tool
                for tool in child_tools
                if getattr(tool, "name", None) not in STORED_RESERVED_TOOL_NAMES
            ]
        restricted_subagents.append(child)
    return _normalize_declarative_subagents_impl(
        restricted_subagents,
        model=model,
        tools=tools,
        backend=backend,
        permissions=parent_child_permissions,
        interrupt_on=interrupt_on,
        skills=skills,
        actor_backend=_actor_backend,
        with_compatibility=compatibility,
        with_actor_summarization=_with_actor_summarization,
        general_purpose_subagent=GENERAL_PURPOSE_SUBAGENT,
        general_purpose_subagent_name=_GENERAL_PURPOSE_SUBAGENT_NAME,
        actor_id_key=_MOLDY_ACTOR_ID_KEY,
        general_purpose_actor=GENERAL_PURPOSE_ACTOR,
    )


def build_agent(
    model: BaseChatModel,
    tools: list[BaseTool],
    system_prompt: str,
    *,
    middleware: list | None = None,
    interrupt_on: dict[str, Any] | bool | None = None,
    checkpointer: Any | None = None,
    store: Any | None = None,
    backend: Any | None = None,
    skills: list[str] | None = None,
    memory: list[str] | None = None,
    permissions: list[FilesystemPermission] | None = None,
    name: str | None = None,
    subagents: list[dict[str, Any]] | None = None,
    runtime_policy: ResolvedRuntimePolicy | None = None,
) -> Any:
    """Build a moldy agent. Returns CompiledStateGraph."""
    effective_permissions = permissions
    effective_tools = tools
    bindings = _DeepAgentFactoryBindings(
        create_deep_agent=create_deep_agent,
        with_compatibility=_with_moldy_deepagents_compatibility,
        normalize_subagents=_normalize_declarative_subagents,
    )
    if runtime_policy is not None and runtime_policy.source == "stored":
        effective_tools = [tool for tool in tools if tool.name not in STORED_RESERVED_TOOL_NAMES]
        effective_permissions = canonicalize_stored_parent_permissions(
            permissions,
            mode=runtime_policy.effective.filesystem.mode,
        )
        filesystem_tool_names = _stored_filesystem_tool_names(runtime_policy)
        todo_enabled = runtime_policy.effective.todo.enabled
        bindings = _DeepAgentFactoryBindings(
            create_deep_agent=create_deep_agent,
            with_compatibility=partial(
                _with_stored_policy_compatibility,
                filesystem_tool_names=filesystem_tool_names,
                todo_enabled=todo_enabled,
            ),
            normalize_subagents=partial(
                _normalize_stored_policy_subagents,
                filesystem_tool_names=filesystem_tool_names,
                todo_enabled=todo_enabled,
            ),
        )
    return _build_agent_impl(
        model=model,
        tools=effective_tools,
        system_prompt=system_prompt,
        middleware=middleware,
        interrupt_on=interrupt_on,
        checkpointer=checkpointer,
        store=store,
        backend=backend,
        skills=skills,
        memory=memory,
        permissions=effective_permissions,
        name=name,
        subagents=subagents,
        bindings=bindings,
    )


def _configured_recursion_limit(cfg: AgentConfig) -> int | None:
    return _configured_recursion_limit_impl(cfg)


def _append_temporal_tools(tools: list[BaseTool]) -> None:
    """Ensure date/time grounding tools are always available to agents."""

    _append_builtin_tools_impl(
        tools,
        keys=_TEMPORAL_BUILTIN_TOOL_KEYS,
        create_builtin_tool=create_builtin_tool,
    )


def _append_e2e_scripted_search_tool(tools: list[BaseTool]) -> None:
    """Attach the deterministic scripted ``tavily_search`` tool for E2E only.

    Gated by ``e2e_scripted_model_enabled`` (which already refuses to run in
    production), so real deployments never see this tool. Mirrors
    ``_append_temporal_tools`` so the E2E search-group fixture
    (``E2E_SEARCH_GROUP``) can emit consecutive search calls whose results the
    frontend aggregates into domain badges + a source count — without any
    network call or product behavior change.
    """

    if not settings.e2e_scripted_model_enabled:
        return
    _append_builtin_tools_impl(
        tools,
        keys=("builtin:e2e_scripted_search",),
        create_builtin_tool=create_builtin_tool,
    )


def _append_e2e_ui_data_demo_tool(tools: list[BaseTool]) -> None:
    """Attach the deterministic generative-UI demo tool for E2E only.

    Gated by ``e2e_scripted_model_enabled`` so real deployments never see it.
    Lets the ``E2E_UI_DATA_DEMO`` fixture drive one ``moldy.ui_data`` event
    (``demo_note``) end-to-end without any product behavior change.
    """

    if not settings.e2e_scripted_model_enabled:
        return
    _append_builtin_tools_impl(
        tools,
        keys=("builtin:e2e_ui_data_demo",),
        create_builtin_tool=create_builtin_tool,
    )


def _add_skill_secrets_to_run(skill_ctx: Any, cfg: AgentConfig) -> None:
    """ADR-021 — union resolved skill credential plaintext into the run set.

    After ``resolve_runtime_credentials`` populates
    ``descriptor.credential_bindings[*].decrypted`` (``dict[str, str]``), feed
    those plaintext values to the run-scoped redaction set. ``add_run_secrets``
    is a no-op when the run ContextVar is unset.

    Also union into ``cfg.secret_values`` — not just the ContextVar copy that
    ``set_run_secrets`` installs. The run worker masks error_message *after* the
    ContextVar is reset, reading ``cfg.secret_values`` (see
    ``conversation_run_worker._redact_run_error_message``), so lazy skill
    credentials must land there too or they'd escape error_message redaction.
    """

    _add_skill_secrets_to_run_impl(
        skill_ctx,
        cfg,
        add_run_secrets=add_run_secrets,
        collect_secret_values=collect_secret_values,
    )


def _selected_skill_slugs(agent_skills: list[dict[str, Any]] | None) -> list[str]:
    return _selected_skill_slugs_impl(agent_skills)


async def _prepare_skill_builder_components(
    cfg: AgentConfig,
    *,
    is_trigger_mode: bool,
    include_ask_user: bool,
    run_id: str | None = None,
    scope_offload_backend: bool = False,
) -> RuntimeComponents:
    """Build the isolated Skill Builder runtime through fresh facade bindings."""

    return await _prepare_skill_builder_components_impl(
        cfg,
        is_trigger_mode=is_trigger_mode,
        include_ask_user=include_ask_user,
        run_id=run_id,
        scope_offload_backend=scope_offload_backend,
        bindings=_runtime_preparation_bindings(),
    )


def _runtime_preparation_bindings() -> _RuntimePreparationBindings:
    """Capture the facade's current preparation collaborators for one call."""

    return _RuntimePreparationBindings(
        data_dir=_DATA_DIR,
        conversation_output_dir=Path(settings.conversation_output_dir),
        deepagent_builtin_types=DEEPAGENT_BUILTIN_TYPES,
        state_backend=StateBackend,
        perf_counter=time.perf_counter,
        system_prompt_with_temporal_context=_system_prompt_with_temporal_context,
        artifact_file_instruction_prompt=_artifact_file_instruction_prompt,
        interactive_tool_instruction_prompt=_interactive_tool_instruction_prompt,
        build_model_candidates=_build_model_candidates,
        build_skill_dependency_tool_configs=build_skill_dependency_tool_configs,
        create_tool_for_runtime=create_tool_for_runtime,
        build_mcp_tools=_build_mcp_tools,
        append_temporal_tools=_append_temporal_tools,
        append_e2e_scripted_search_tool=_append_e2e_scripted_search_tool,
        append_e2e_ui_data_demo_tool=_append_e2e_ui_data_demo_tool,
        memory_write_policy_for_run=_memory_write_policy_for_run,
        build_memory_tools=build_memory_tools,
        resolve_middleware_model_params=_resolve_middleware_model_params,
        build_default_reliability_middleware=_build_default_reliability_middleware,
        build_middleware_instances=build_middleware_instances,
        get_provider_middleware=get_provider_middleware,
        scoped_runtime_backend=_scoped_runtime_backend,
        build_skill_runtime_context=build_skill_runtime_context,
        runtime_db_session_factory=_runtime_db_session_factory_impl,
        resolve_runtime_credentials=resolve_runtime_credentials,
        add_skill_secrets_to_run=_add_skill_secrets_to_run,
        create_skill_execute_tool=_create_skill_execute_tool,
        build_skills_prompt=_build_runtime_skills_prompt_impl,
        memory_tool_instruction_prompt=_memory_tool_instruction_prompt,
        load_memory_context=_load_memory_context,
        build_filesystem_permissions=build_filesystem_permissions,
        build_stored_filesystem_permissions=build_stored_filesystem_permissions,
        selected_skill_slugs=_selected_skill_slugs,
        ask_user_tool=ask_user_tool,
        build_interrupt_on_policy=_build_interrupt_on_policy,
        load_skill_builder_prompt=load_skill_builder_prompt,
        build_skill_builder_tools=build_skill_builder_tools,
        session_consent_eligible_tools=_SESSION_CONSENT_ELIGIBLE_TOOLS,
    )


async def _prepare_runtime_components(
    cfg: AgentConfig,
    *,
    is_trigger_mode: bool,
    include_ask_user: bool,
    include_agent_memory_file: bool,
    timings: dict[str, int] | None = None,
    run_id: str | None = None,
    scope_offload_backend: bool = False,
) -> RuntimeComponents:
    """Build reusable Deep Agents runtime pieces for a parent or child agent."""
    if cfg.runtime_profile == "skill_builder":
        return await _prepare_skill_builder_components(
            cfg,
            is_trigger_mode=is_trigger_mode,
            include_ask_user=include_ask_user,
            run_id=run_id,
            scope_offload_backend=scope_offload_backend,
        )
    return await _prepare_runtime_components_impl(
        cfg,
        is_trigger_mode=is_trigger_mode,
        include_ask_user=include_ask_user,
        include_agent_memory_file=include_agent_memory_file,
        timings=timings,
        run_id=run_id,
        scope_offload_backend=scope_offload_backend,
        bindings=_runtime_preparation_bindings(),
    )


async def _prepare_agent(
    cfg: AgentConfig,
    *,
    messages_history: list[dict[str, str]],
    is_trigger_mode: bool = False,
    run_id: str | None = None,
) -> tuple[Any, list, dict]:
    """에이전트 빌드 + 설정. stream/invoke 공용.

    ``is_trigger_mode=True`` 는 트리거(invoke) 모드 indicator — 사용자가 없으므로
    (a) ``ask_user`` 도구 미주입(호출 시 영원히 hang), (b) HiTL ``interrupt_on``
    을 None 으로 강제 override 하여 위험 도구 승인 게이트도 자동 통과.
    """
    prepare_started = time.perf_counter()
    last_mark = prepare_started
    timings: dict[str, int] = {}

    def mark_timing(name: str) -> None:
        nonlocal last_mark
        now = time.perf_counter()
        timings[name] = int((now - last_mark) * 1000)
        last_mark = now

    components = await _prepare_runtime_components(
        cfg,
        is_trigger_mode=is_trigger_mode,
        include_ask_user=not is_trigger_mode,
        include_agent_memory_file=True,
        timings=timings,
        run_id=run_id,
        scope_offload_backend=run_id is not None,
    )
    last_mark = time.perf_counter()

    # 5. 에이전트 빌드 — create_deep_agent + checkpointer
    from app.agent_runtime.checkpointer import get_checkpointer

    build_started = time.perf_counter()
    agent = build_agent(
        components.model,
        components.tools,
        components.system_prompt,
        middleware=components.middleware or None,
        interrupt_on=components.interrupt_on,
        checkpointer=get_checkpointer(),
        backend=components.backend,
        skills=components.skills_sources,
        memory=components.memory_sources,
        permissions=components.permissions,
        name=cfg.agent_runtime_name or f"agent_{cfg.thread_id[:8]}",
        subagents=cfg.subagents_config,
        runtime_policy=cfg.runtime_policy,
    )
    timings["build_agent_ms"] = int((time.perf_counter() - build_started) * 1000)
    last_mark = time.perf_counter()

    lc_messages = convert_to_langchain_messages(messages_history)
    mark_timing("messages_ms")
    config: dict[str, Any] = {"configurable": {"thread_id": cfg.thread_id}}
    recursion_limit = _configured_recursion_limit(cfg)
    if recursion_limit is not None:
        config["recursion_limit"] = recursion_limit
    if cfg.checkpoint_id:
        # LangGraph time-travel: invoking with an explicit checkpoint_id forks
        # a new branch from that point. The new run's checkpoints chain back to
        # this id, and `alist` reveals both branches as siblings of the parent.
        config["configurable"]["checkpoint_id"] = cfg.checkpoint_id

    timings["total_ms"] = int((time.perf_counter() - prepare_started) * 1000)
    timing_payload = " ".join(f"{key}={value}" for key, value in timings.items())
    log_message = (
        "agent_prepare_timing "
        f"agent_id={cfg.agent_id} thread_id={cfg.thread_id} "
        f"tools={len(components.tools)} skills={len(cfg.agent_skills or [])} "
        f"{timing_payload}"
    )
    logger.debug(log_message)
    if timings["total_ms"] >= 250:
        logger.info(log_message)

    return agent, lc_messages, config
