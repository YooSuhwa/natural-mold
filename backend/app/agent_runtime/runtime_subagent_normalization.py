"""Subagent normalization for Moldy's Deep Agents build boundary.

Collaborators that tests patch on ``runtime_component_builder`` are resolved
through the live facade at call time.
"""

from __future__ import annotations

import uuid
from functools import partial
from typing import Any

from deepagents.middleware.filesystem import FilesystemPermission
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
from deepagents.middleware.summarization import create_summarization_middleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from app.agent_runtime.deep_agent_factory import (
    actor_backend_impl as _actor_backend_impl,
)
from app.agent_runtime.deep_agent_factory import (
    normalize_declarative_subagents_impl as _normalize_declarative_subagents_impl,
)
from app.agent_runtime.deep_agent_factory import (
    with_actor_summarization_impl as _with_actor_summarization_impl,
)
from app.agent_runtime.offload_storage import GENERAL_PURPOSE_ACTOR
from app.agent_runtime.runtime_facade_bindings import runtime_attr
from app.agent_runtime.runtime_policy_capabilities import (
    STORED_RESERVED_TOOL_NAMES,
    RestrictedSubagentSpecError,
    sanitize_child_filesystem_permissions,
)

_SUMMARIZATION_MIDDLEWARE_NAME = "SummarizationMiddleware"
_GENERAL_PURPOSE_SUBAGENT_NAME = GENERAL_PURPOSE_SUBAGENT["name"]
_MOLDY_ACTOR_ID_KEY = "_moldy_actor_id"


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
        middleware_name=runtime_attr("_middleware_name"),
        create_summarization_middleware=create_summarization_middleware,
        summarization_middleware_name=_SUMMARIZATION_MIDDLEWARE_NAME,
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
        actor_backend=runtime_attr("_actor_backend"),
        with_compatibility=runtime_attr("_with_moldy_deepagents_compatibility"),
        with_actor_summarization=runtime_attr("_with_actor_summarization"),
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
        runtime_attr("_with_stored_policy_compatibility"),
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
        actor_backend=runtime_attr("_actor_backend"),
        with_compatibility=compatibility,
        with_actor_summarization=runtime_attr("_with_actor_summarization"),
        general_purpose_subagent=GENERAL_PURPOSE_SUBAGENT,
        general_purpose_subagent_name=_GENERAL_PURPOSE_SUBAGENT_NAME,
        actor_id_key=_MOLDY_ACTOR_ID_KEY,
        general_purpose_actor=GENERAL_PURPOSE_ACTOR,
    )
