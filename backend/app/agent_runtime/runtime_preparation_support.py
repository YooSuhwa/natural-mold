"""Typed bindings and small helpers for runtime component preparation."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Iterable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from deepagents.backends import BackendProtocol
from deepagents.middleware.filesystem import FilesystemPermission
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.offload_storage import (
    OffloadIdentity,
    ScopedOffloadBackend,
    ScopedOffloadStorage,
)
from app.agent_runtime.runtime_config import AgentConfig
from app.marketplace.skill_runtime import SkillToolContext


class BuildSkillBuilderTools(Protocol):
    def __call__(
        self,
        *,
        session_id: str,
        workspace_path: str,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
        user_id: str | None,
        agent_id: str | None,
        credential_subject_user_id: str | None,
        include_runtime_tools: bool,
        consented_tools: Sequence[str] | None,
    ) -> list[BaseTool]: ...


class BuildSkillDependencyTools(Protocol):
    def __call__(
        self,
        *,
        agent_skills: list[dict[str, Any]],
        existing_tool_configs: list[dict[str, Any]],
        user_id: str | None,
        agent_id: str | None,
    ) -> list[dict[str, Any]]: ...


class MemoryWritePolicy(Protocol):
    def __call__(self, cfg: AgentConfig, *, is_trigger_mode: bool) -> Awaitable[str]: ...


class BuildMemoryTools(Protocol):
    def __call__(
        self,
        *,
        user_id: str,
        agent_id: str | None,
        conversation_id: str,
        is_trigger_mode: bool,
    ) -> list[BaseTool]: ...


class BuildReliabilityMiddleware(Protocol):
    def __call__(
        self,
        model_candidates: list[BaseChatModel],
        *,
        configured_types: set[str],
    ) -> list[AgentMiddleware]: ...


class ScopedBackendFactory(Protocol):
    def __call__(self, cfg: AgentConfig, *, run_id: str | None) -> ScopedOffloadBackend: ...


class BuildSkillRuntimeContext(Protocol):
    def __call__(
        self,
        cfg: AgentConfig,
        *,
        data_dir: Path,
        output_root: Path,
    ) -> SkillToolContext: ...


class ResolveRuntimeCredentials(Protocol):
    def __call__(
        self,
        ctx: SkillToolContext,
        *,
        db: AsyncSession,
        cfg: AgentConfig,
    ) -> Awaitable[None]: ...


class BuildFilesystemPermissions(Protocol):
    def __call__(
        self,
        *,
        thread_id: str,
        agent_id: str | None,
        user_id: str | None,
        selected_skill_slugs: list[str],
        agent_runtime_name: str | None,
        draft_workspace_path: str | None = None,
    ) -> list[FilesystemPermission]: ...


class BuildStoredFilesystemPermissions(Protocol):
    def __call__(
        self,
        *,
        thread_id: str,
        agent_id: str | None,
        user_id: str | None,
        selected_skill_slugs: list[str],
        agent_runtime_name: str | None,
        include_agent_memory_file: bool,
        mode: Literal["inspect", "artifact_write"],
    ) -> list[FilesystemPermission]: ...


class BuildInterruptPolicy(Protocol):
    def __call__(
        self,
        middleware_configs: list[dict[str, Any]] | None,
        tools: list[BaseTool],
        *,
        include_ask_user: bool,
        is_trigger_mode: bool,
    ) -> dict[str, Any] | None: ...


@dataclass(frozen=True, slots=True)
class RuntimePreparationBindings:
    """Per-call snapshot of the facade collaborators used during preparation."""

    data_dir: Path
    conversation_output_dir: Path
    deepagent_builtin_types: frozenset[str]
    state_backend: Callable[[], BackendProtocol]
    perf_counter: Callable[[], float]
    system_prompt_with_temporal_context: Callable[[str], str]
    artifact_file_instruction_prompt: Callable[[str], str]
    interactive_tool_instruction_prompt: Callable[[], str]
    build_model_candidates: Callable[[AgentConfig], list[BaseChatModel]]
    build_skill_dependency_tool_configs: BuildSkillDependencyTools
    create_tool_for_runtime: Callable[[dict[str, Any]], BaseTool | None]
    build_mcp_tools: Callable[[list[dict[str, Any]]], Awaitable[list[BaseTool]]]
    append_temporal_tools: Callable[[list[BaseTool]], None]
    append_e2e_scripted_search_tool: Callable[[list[BaseTool]], None]
    append_e2e_ui_data_demo_tool: Callable[[list[BaseTool]], None]
    memory_write_policy_for_run: MemoryWritePolicy
    build_memory_tools: BuildMemoryTools
    resolve_middleware_model_params: Callable[
        [list[dict[str, Any]], dict[str, str | None]], list[dict[str, Any]]
    ]
    build_default_reliability_middleware: BuildReliabilityMiddleware
    build_middleware_instances: Callable[[list[dict[str, Any]]], list[AgentMiddleware]]
    get_provider_middleware: Callable[[str], list[AgentMiddleware]]
    scoped_runtime_backend: ScopedBackendFactory
    build_skill_runtime_context: BuildSkillRuntimeContext
    runtime_db_session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]]
    resolve_runtime_credentials: ResolveRuntimeCredentials
    add_skill_secrets_to_run: Callable[[SkillToolContext, AgentConfig], None]
    create_skill_execute_tool: Callable[[SkillToolContext], BaseTool]
    build_skills_prompt: Callable[[list[dict[str, Any]]], str]
    memory_tool_instruction_prompt: Callable[[], str]
    load_memory_context: Callable[[AgentConfig], Awaitable[tuple[str, list[dict[str, Any]]]]]
    build_filesystem_permissions: BuildFilesystemPermissions
    build_stored_filesystem_permissions: BuildStoredFilesystemPermissions
    selected_skill_slugs: Callable[[list[dict[str, Any]] | None], list[str]]
    ask_user_tool: BaseTool
    build_interrupt_on_policy: BuildInterruptPolicy
    load_skill_builder_prompt: Callable[[str], str]
    build_skill_builder_tools: BuildSkillBuilderTools
    session_consent_eligible_tools: frozenset[str]


def runtime_db_session_factory() -> AbstractAsyncContextManager[AsyncSession]:
    """Resolve the owning database session factory at operation time."""

    from app.database import async_session

    return async_session()


def build_runtime_skills_prompt(agent_skills: list[dict[str, Any]]) -> str:
    """Resolve the owning skill prompt builder at operation time."""

    from app.skills.prompt import build_skills_prompt

    return build_skills_prompt(agent_skills)


def scoped_runtime_backend_impl(
    cfg: AgentConfig,
    *,
    run_id: str | None,
    data_dir: Path,
    db_free_actor_id: uuid.UUID,
) -> ScopedOffloadBackend:
    """Build the trusted owner/conversation/run/actor storage boundary."""

    if run_id is None:
        raise ValueError("run_id is required when scoped offload storage is enabled")
    owner_id = cfg.agent_owner_user_id or cfg.user_id or "db-free"
    if cfg.agent_id is not None:
        try:
            actor_id = uuid.UUID(cfg.agent_id)
        except ValueError:
            actor_id = uuid.uuid5(uuid.NAMESPACE_URL, f"moldy-agent:{cfg.agent_id}")
    else:
        actor_id = db_free_actor_id
    storage = ScopedOffloadStorage(
        data_dir=data_dir,
        identity=OffloadIdentity(
            owner_id=owner_id,
            conversation_id=cfg.thread_id,
            run_id=run_id,
        ),
    )
    return storage.for_actor(actor_id)


def configured_recursion_limit_impl(cfg: AgentConfig) -> int | None:
    raw = (cfg.model_params or {}).get("recursion_limit")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def append_builtin_tools_impl(
    tools: list[BaseTool],
    *,
    keys: tuple[str, ...],
    create_builtin_tool: Callable[[str], BaseTool | None],
) -> None:
    existing = {tool.name for tool in tools}
    for key in keys:
        tool = create_builtin_tool(key)
        if tool is not None and tool.name not in existing:
            tools.append(tool)
            existing.add(tool.name)


def add_skill_secrets_to_run_impl(
    skill_ctx: SkillToolContext,
    cfg: AgentConfig,
    *,
    add_run_secrets: Callable[[Iterable[str] | None], None],
    collect_secret_values: Callable[[object], set[str]],
) -> None:
    for descriptor in getattr(skill_ctx, "descriptors", {}).values():
        for binding in getattr(descriptor, "credential_bindings", {}).values():
            decrypted = getattr(binding, "decrypted", None)
            add_run_secrets(decrypted)
            cfg.secret_values.update(collect_secret_values(decrypted))


def selected_skill_slugs_impl(agent_skills: list[dict[str, Any]] | None) -> list[str]:
    return [
        slug for raw in agent_skills or [] if isinstance((slug := raw.get("slug")), str) and slug
    ]
