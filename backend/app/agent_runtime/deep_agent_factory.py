"""Deep Agents graph construction behind Moldy's live compatibility facade."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

from deepagents.backends import BackendProtocol, StateBackend
from deepagents.middleware.async_subagents import AsyncSubAgent
from deepagents.middleware.filesystem import FilesystemMiddleware, FilesystemPermission
from deepagents.middleware.subagents import CompiledSubAgent, SubAgent
from deepagents.middleware.summarization import create_summarization_middleware
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
from app.agent_runtime.runtime.connection_guard_order import order_connection_guard
from app.approvals.unattended import UnattendedMode, apply_unattended
from app.guardrails.approval import has_approval_rules, with_approvals
from app.guardrails.middleware import Guard, guard_stack
from app.guardrails.tool_risk import ToolRiskPolicy, apply_to_agent

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
        if (enforced := _agent_permissions(child_middleware, child_permissions)) is not None:
            child["permissions"] = enforced
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
            permissions=_agent_permissions(general_purpose_middleware, permissions),
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


def _guarded(
    middleware: list[Any],
    subagents: list[dict[str, Any]],
    guard: Guard,
    model: BaseChatModel,
    backend: Any,
) -> tuple[list[Any], list[dict[str, Any]]]:
    """The root and every declarative child behind the same guardrail (plan §3.4.1).

    Deep Agents passes root middleware only to forked children, so each child gets its own
    copy; its summarizer is wrapped too. Precompiled and async specs are left alone (the
    prepared path does not run with a guard).
    """
    guarded_root = guard_stack(
        middleware,
        guard,
        create_summarizer=lambda: create_summarization_middleware(model, backend),
    )
    children: list[dict[str, Any]] = []
    for spec in subagents:
        if "runnable" in spec or "graph_id" in spec:
            children.append(spec)
            continue
        child_model = cast(BaseChatModel, spec.get("model", model))
        children.append(
            {
                **spec,
                "middleware": guard_stack(
                    spec.get("middleware") or [],
                    guard,
                    create_summarizer=lambda m=child_model: create_summarization_middleware(
                        m, backend
                    ),
                ),
            }
        )
    return guarded_root, children


def _with_child_approvals(
    subagents: list[dict[str, Any]], parent: dict[str, Any], guard: Guard
) -> list[dict[str, Any]]:
    children: list[dict[str, Any]] = []
    for spec in subagents:
        own = spec.get("interrupt_on", parent)
        if "runnable" in spec or "graph_id" in spec or not isinstance(own, dict):
            children.append(spec)
            continue
        tools = list(spec.get("tools") or [])
        children.append({**spec, "interrupt_on": with_approvals(own, tools, guard.rules)})
    return children


def _agent_permissions(
    middleware: Sequence[Any], permissions: list[FilesystemPermission] | None
) -> list[FilesystemPermission] | None:
    """The rules Moldy's filesystem layer enforces, which Deep Agents requires at both levels.

    The layer adds the scoped offload read rules, and Deep Agents 0.7.30+ refuses a
    filesystem middleware whose rules differ from the agent's.
    """
    if permissions is None or not isinstance(middleware, (list, tuple)):
        return permissions
    for item in middleware:
        if isinstance(item, FilesystemMiddleware) and item._permissions:  # noqa: SLF001
            return list(item._permissions)  # noqa: SLF001
    return permissions


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
    guard: Guard | None = None,
    tool_risk: ToolRiskPolicy | None = None,
    unattended: UnattendedMode | None = None,
    response_format: Any | None = None,
    tool_approval: Any | None = None,
) -> Any:
    resolved_backend = backend if backend is not None else StateBackend()
    compatible_middleware = bindings.with_compatibility(
        middleware,
        backend=resolved_backend,
        permissions=permissions,
    )
    if prepared_task_middleware is not None:
        if tool_approval is not None and tool_approval.acts:
            # Lease eligibility sends such runs down the ordinary path (``tool_approval``).
            raise ValueError("prepared runs do not carry tool approval settings")
        # Prepared leases: every child is already a compiled base child (including
        # general-purpose) and the interceptor is the first app tool-call wrapper.
        if not subagents or any("runnable" not in spec for spec in subagents):
            raise ValueError("prepared runs require precompiled subagents")
        compatible_middleware = [prepared_task_middleware, *compatible_middleware]
        compatible_subagents = list(subagents)
        # Children were compiled with their own guardrail and tool risk stacks
        # (prepared_run_compile); here only the root's.
        if tool_risk is not None:
            applied = apply_to_agent(
                tool_risk,
                tools=tools,
                interrupt_on=interrupt_on,
                middleware=compatible_middleware,
                subagents=[],
            )
            tools, interrupt_on = applied.tools, applied.interrupt_on
            compatible_middleware = applied.middleware
        if guard is not None:
            if has_approval_rules(guard.rules):
                # Approval rules ride interrupt_on predicates; the lease falls back.
                raise ValueError("prepared runs do not carry guardrail approval rules")
            compatible_middleware = guard_stack(
                compatible_middleware,
                guard,
                create_summarizer=lambda: create_summarization_middleware(model, resolved_backend),
            )
    else:
        # F-07: the interactive ConnectionGuard checks before the HITL approval step.
        compatible_middleware = order_connection_guard(
            compatible_middleware, interrupt_on=interrupt_on, permissions=permissions
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
        if tool_risk is not None:
            # Company actions per tool risk level, for the root and each child (C5).
            applied = apply_to_agent(
                tool_risk,
                tools=tools,
                interrupt_on=interrupt_on,
                middleware=list(compatible_middleware),
                subagents=compatible_subagents,
            )
            tools, interrupt_on = applied.tools, applied.interrupt_on
            compatible_middleware, compatible_subagents = applied.middleware, applied.subagents
        if tool_approval is not None and tool_approval.acts:
            # The root agent's own tool approval settings, under the company step (plan 3.1).
            from app.approvals import tool_modes

            settled = tool_modes.apply(interrupt_on, tools, tool_approval)
            interrupt_on = settled.interrupt_on
            if tool_approval.defaults or (tool_approval.company and tool_approval.company.tools):
                # A child's tools keep the company's per-tool policy, and their own defaults
                # where they tighten.
                compatible_subagents = [
                    spec
                    if "runnable" in spec
                    or "graph_id" in spec
                    or not isinstance(spec.get("interrupt_on"), dict)
                    else {
                        **spec,
                        "interrupt_on": tool_modes.apply(
                            spec["interrupt_on"],
                            spec.get("tools") or [],
                            tool_approval,
                            root=False,
                        ).interrupt_on,
                    }
                    for spec in compatible_subagents
                ]
            if settled.auto_allowed and tool_approval.audit is not None:
                compatible_middleware = [
                    *compatible_middleware,
                    tool_modes.AutoAllowedAuditMiddleware(
                        settled.auto_allowed, tool_approval.audit
                    ),
                ]
        if guard is not None and has_approval_rules(guard.rules) and isinstance(interrupt_on, dict):
            # Rules that ask before a tool runs ride the standard approval path (③).
            interrupt_on = with_approvals(interrupt_on, tools, guard.rules)
            compatible_subagents = _with_child_approvals(compatible_subagents, interrupt_on, guard)
        if guard is not None:
            compatible_middleware, compatible_subagents = _guarded(
                compatible_middleware, compatible_subagents, guard, model, resolved_backend
            )
        if unattended is not None:
            # Nobody watches the run: approvals wait for the inbox or answer BLOCKED (17.3).
            interrupt_on, compatible_middleware, compatible_subagents = apply_unattended(
                unattended,
                interrupt_on=interrupt_on,
                middleware=list(compatible_middleware),
                subagents=compatible_subagents,
            )
    # Structured output (workflow W0) is passed only when asked for, so factories and
    # test doubles that predate it keep their signature.
    extra: dict[str, Any] = {} if response_format is None else {"response_format": response_format}
    return bindings.create_deep_agent(
        **extra,
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
        permissions=_agent_permissions(compatible_middleware, permissions),
        name=name,
        subagents=cast(Sequence[_DeepAgentSubagent], compatible_subagents),
    )
