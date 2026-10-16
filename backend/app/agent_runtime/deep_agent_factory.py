"""Deep Agents graph construction behind Moldy's live compatibility facade."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

from deepagents.backends import BackendProtocol, StateBackend
from deepagents.middleware.async_subagents import AsyncSubAgent
from deepagents.middleware.filesystem import FilesystemPermission
from deepagents.middleware.subagents import CompiledSubAgent, SubAgent
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.human_in_the_loop import InterruptOnConfig
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.store.base import BaseStore
from langgraph.types import Checkpointer

from app.agent_runtime.deep_agent_middleware_factory import (
    build_moldy_filesystem_middleware_impl as build_moldy_filesystem_middleware_impl,
)
from app.agent_runtime.deep_agent_middleware_factory import (
    middleware_name_impl as middleware_name_impl,
)
from app.agent_runtime.deep_agent_middleware_factory import (
    with_actor_summarization_impl as with_actor_summarization_impl,
)
from app.agent_runtime.deep_agent_middleware_factory import (
    with_moldy_deepagents_compatibility_impl as with_moldy_deepagents_compatibility_impl,
)
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


@dataclass(frozen=True, slots=True)
class DeepAgentFactoryBindings:
    create_deep_agent: _CreateDeepAgent
    with_compatibility: _CompatibilityBuilder
    normalize_subagents: _SubagentNormalizer


def actor_backend_impl(backend: Any, actor_id: Any) -> Any:
    if not isinstance(backend, ScopedOffloadBackend):
        return backend
    if actor_id is None:
        raise ValueError("declarative subagent is missing its trusted actor identity")
    return backend.for_actor(actor_id)


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
    prepared_task_middleware: AgentMiddleware | None = None,
) -> Any:
    resolved_backend = backend if backend is not None else StateBackend()
    compatible_middleware = bindings.with_compatibility(
        middleware,
        backend=resolved_backend,
        permissions=permissions,
    )
    if prepared_task_middleware is not None:
        # Prepared leases: every child is already a compiled base child (including
        # general-purpose) and the interceptor is the first app tool-call wrapper.
        if not subagents or any("runnable" not in spec for spec in subagents):
            raise ValueError("prepared runs require precompiled subagents")
        compatible_middleware = [prepared_task_middleware, *compatible_middleware]
        compatible_subagents = list(subagents)
    else:
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
