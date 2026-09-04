"""Deep Agents graph construction behind Moldy's live compatibility facade."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

from deepagents.backends import BackendProtocol, StateBackend
from deepagents.middleware.async_subagents import AsyncSubAgent
from deepagents.middleware.filesystem import FilesystemMiddleware, FilesystemPermission, FsToolName
from deepagents.middleware.subagents import CompiledSubAgent, SubAgent
from langchain.agents.middleware import AgentMiddleware, TodoListMiddleware
from langchain.agents.middleware.human_in_the_loop import InterruptOnConfig
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.store.base import BaseStore
from langgraph.types import Checkpointer

from app.agent_runtime.filesystem_permission_middleware import MoldyFilesystemMiddleware
from app.agent_runtime.offload_storage import ScopedOffloadBackend

type _DeepAgentSubagent = SubAgent | CompiledSubAgent | AsyncSubAgent


class _CompatibilityBuilder(Protocol):
    def __call__(
        self,
        middleware: list[Any] | tuple[Any, ...] | None,
        *,
        backend: Any,
        permissions: list[FilesystemPermission] | None,
    ) -> list[Any]: ...


class _SubagentNormalizer(Protocol):
    def __call__(
        self,
        subagents: list[dict[str, Any]] | None,
        *,
        model: BaseChatModel,
        tools: list[BaseTool],
        backend: Any,
        permissions: list[FilesystemPermission] | None,
        interrupt_on: dict[str, Any] | bool | None,
        skills: list[str] | None,
    ) -> list[dict[str, Any]]: ...


class _ActorSummarizationBuilder(Protocol):
    def __call__(
        self,
        middleware: list[Any],
        *,
        model: BaseChatModel,
        backend: Any,
    ) -> list[Any]: ...


class _CreateDeepAgent(Protocol):
    def __call__(
        self,
        model: BaseChatModel,
        tools: Sequence[BaseTool],
        *,
        system_prompt: str,
        middleware: Sequence[AgentMiddleware],
        interrupt_on: dict[str, bool | InterruptOnConfig] | None,
        checkpointer: Checkpointer | None,
        store: BaseStore | None,
        backend: BackendProtocol,
        skills: list[str] | None,
        memory: list[str] | None,
        permissions: list[FilesystemPermission] | None,
        name: str | None,
        subagents: Sequence[_DeepAgentSubagent] | None,
    ) -> Any: ...


class _FilesystemMiddlewareBuilder(Protocol):
    def __call__(
        self,
        *,
        backend: BackendProtocol,
        permissions: list[FilesystemPermission] | None,
    ) -> FilesystemMiddleware: ...


@dataclass(frozen=True, slots=True)
class DeepAgentFactoryBindings:
    create_deep_agent: _CreateDeepAgent
    with_compatibility: _CompatibilityBuilder
    normalize_subagents: _SubagentNormalizer


def middleware_name_impl(middleware: Any) -> str | None:
    name = getattr(middleware, "name", None)
    return name if isinstance(name, str) else None


def build_moldy_filesystem_middleware_impl(
    *,
    backend: Any,
    permissions: list[FilesystemPermission] | None,
    filesystem_tool_names: tuple[str, ...],
    add_scoped_permissions: Callable[[list[FilesystemPermission], Any], list[FilesystemPermission]],
) -> FilesystemMiddleware:
    effective_permissions = permissions
    if permissions is not None and isinstance(backend, ScopedOffloadBackend):
        effective_permissions = add_scoped_permissions(permissions, backend.artifacts_root)
    return MoldyFilesystemMiddleware(
        backend=backend,
        tools=cast(list[FsToolName], list(filesystem_tool_names)),
        permissions=effective_permissions,
    )


def with_moldy_deepagents_compatibility_impl(
    middleware: list[Any] | tuple[Any, ...] | None,
    *,
    backend: Any,
    permissions: list[FilesystemPermission] | None,
    middleware_name: Callable[[Any], str | None],
    build_filesystem_middleware: _FilesystemMiddlewareBuilder,
    filesystem_middleware_name: str,
    todo_list_middleware_name: str,
    include_todo: bool = True,
) -> list[Any]:
    excluded_names = {filesystem_middleware_name, todo_list_middleware_name}
    retained = [item for item in (middleware or ()) if middleware_name(item) not in excluded_names]
    filesystem = build_filesystem_middleware(
        backend=cast(BackendProtocol, backend),
        permissions=permissions,
    )
    compatibility_middleware: list[Any] = [filesystem]
    if include_todo:
        compatibility_middleware.append(TodoListMiddleware())
    return [*compatibility_middleware, *retained]


def actor_backend_impl(backend: Any, actor_id: Any) -> Any:
    if not isinstance(backend, ScopedOffloadBackend):
        return backend
    if actor_id is None:
        raise ValueError("declarative subagent is missing its trusted actor identity")
    return backend.for_actor(actor_id)


def with_actor_summarization_impl(
    middleware: list[Any],
    *,
    model: BaseChatModel,
    backend: Any,
    middleware_name: Callable[[Any], str | None],
    create_summarization_middleware: Callable[[BaseChatModel, Any], Any],
    summarization_middleware_name: str,
) -> list[Any]:
    if not isinstance(backend, ScopedOffloadBackend):
        return middleware
    retained = [
        item for item in middleware if middleware_name(item) != summarization_middleware_name
    ]
    return [*retained, create_summarization_middleware(model, backend)]


def normalize_declarative_subagents_impl(
    subagents: list[dict[str, Any]] | None,
    *,
    model: BaseChatModel,
    tools: list[BaseTool],
    backend: Any,
    permissions: list[FilesystemPermission] | None,
    interrupt_on: dict[str, Any] | bool | None,
    skills: list[str] | None,
    actor_backend: Callable[[Any, Any], Any],
    with_compatibility: _CompatibilityBuilder,
    with_actor_summarization: _ActorSummarizationBuilder,
    general_purpose_subagent: Mapping[str, Any],
    general_purpose_subagent_name: str,
    actor_id_key: str,
    general_purpose_actor: Any,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    has_general_purpose = False
    for spec in subagents or ():
        if spec.get("name") == general_purpose_subagent_name:
            has_general_purpose = True

        if "runnable" in spec or "graph_id" in spec:
            normalized.append(spec)
            continue

        child_permissions = spec.get("permissions", permissions)
        child = dict(spec)
        default_actor = (
            general_purpose_actor if spec.get("name") == general_purpose_subagent_name else None
        )
        actor_id = child.pop(actor_id_key, default_actor)
        if isinstance(backend, ScopedOffloadBackend) and actor_id is None:
            raise ValueError("declarative subagent is missing its trusted actor identity")
        child_backend = actor_backend(backend, actor_id)
        child_model = cast(BaseChatModel, child.get("model", model))
        child_middleware = with_compatibility(
            spec.get("middleware"),
            backend=child_backend,
            permissions=child_permissions,
        )
        child["middleware"] = with_actor_summarization(
            child_middleware,
            model=child_model,
            backend=child_backend,
        )
        normalized.append(child)

    if not has_general_purpose:
        general_purpose_backend = actor_backend(backend, general_purpose_actor)
        general_purpose_middleware = with_compatibility(
            None,
            backend=general_purpose_backend,
            permissions=permissions,
        )
        general_purpose = dict(
            general_purpose_subagent,
            model=model,
            tools=list(tools),
            middleware=general_purpose_middleware,
            permissions=permissions,
            interrupt_on=interrupt_on,
        )
        general_purpose["middleware"] = with_actor_summarization(
            general_purpose_middleware,
            model=model,
            backend=general_purpose_backend,
        )
        if skills is not None:
            general_purpose["skills"] = list(skills)
        normalized.insert(0, general_purpose)

    return normalized


def build_agent_impl(
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
    bindings: DeepAgentFactoryBindings,
) -> Any:
    resolved_backend = backend if backend is not None else StateBackend()
    compatible_middleware = bindings.with_compatibility(
        middleware,
        backend=resolved_backend,
        permissions=permissions,
    )
    compatible_subagents = bindings.normalize_subagents(
        subagents,
        model=model,
        tools=tools,
        backend=resolved_backend,
        permissions=permissions,
        interrupt_on=interrupt_on,
        skills=skills,
    )
    return bindings.create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=cast(Sequence[AgentMiddleware], compatible_middleware),
        interrupt_on=cast(dict[str, bool | InterruptOnConfig] | None, interrupt_on),
        checkpointer=cast(Checkpointer | None, checkpointer),
        store=cast(BaseStore | None, store),
        backend=cast(BackendProtocol, resolved_backend),
        skills=skills,
        memory=memory,
        permissions=permissions,
        name=name,
        subagents=cast(Sequence[_DeepAgentSubagent], compatible_subagents),
    )
