"""Immutable prepared child catalog for leased runs (parent Task 10, internal I12).

The effective child closure is normalized recursively before the
materialization marker: stored linked children, trusted compiled children
(identified by object identity, never by name, and verified against stored
policy), the implicit ``general-purpose`` child, and nested children. Every
child receives a stable ID (its path), a token over its canonical record and a
frozen set of response-format variant IDs; the catalog digest covers all of
it. Response formats are canonicalized in ``prepared_response_variants``.
Child names must match a strict ``/``-free pattern, so child IDs cannot
collide. No child may declare or expose a ``task`` tool: a trusted compiled
graph is enumerated recursively (subgraphs included) and fails closed when its
tools cannot be enumerated, so it can never hide a stock task handler.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

from deepagents.middleware.subagents import DEFAULT_SUBAGENT_PROMPT, GENERAL_PURPOSE_SUBAGENT

from app.agent_runtime.prepared_response_variants import DEFAULT_VARIANT as DEFAULT_VARIANT
from app.agent_runtime.prepared_response_variants import (
    PreparedChildCatalogError as PreparedChildCatalogError,
)
from app.agent_runtime.prepared_response_variants import canonical_sha as _sha
from app.agent_runtime.prepared_response_variants import (
    response_variant_id as response_variant_id,
)

ROOT_PATH = "root"
GENERAL_PURPOSE_NAME: str = GENERAL_PURPOSE_SUBAGENT["name"]
NESTED_CHILDREN_KEY = "subagents"
RESPONSE_VARIANTS_KEY = "_moldy_response_variants"
TASK_TOOL_NAME = "task"
ACTOR_ID_KEY = "_moldy_actor_id"
_CATALOG_KEYS = frozenset({NESTED_CHILDREN_KEY, RESPONSE_VARIANTS_KEY, "_moldy_origin"})
_CHILD_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
ChildOrigin = Literal["declarative", "trusted_compiled", "general_purpose"]


@dataclass(frozen=True, slots=True)
class PreparedChildPolicy:
    """Stored policy one child must match (keyed by child ID)."""

    model: str
    tools: frozenset[str]
    permissions: tuple[str, ...] = ()
    response_formats: tuple[Any, ...] = ()


@dataclass(frozen=True, slots=True)
class TrustedCompiledChild:
    """An app-compiled child accepted only by exact runnable identity."""

    runnable: Any = field(repr=False)
    model: str
    tools: frozenset[str]
    permissions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PreparedChild:
    child_id: str
    parent_path: str
    name: str
    description: str
    origin: ChildOrigin
    model: str
    tools: tuple[str, ...]
    permissions: tuple[str, ...]
    variants: tuple[str, ...]
    children: tuple[str, ...]
    token: str
    spec: Mapping[str, Any] = field(compare=False, repr=False)
    variant_values: Mapping[str, Any] = field(compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class PreparedChildCatalog:
    children: Mapping[str, PreparedChild]
    digest: str

    def level(self, parent_path: str) -> Mapping[str, PreparedChild]:
        return MappingProxyType(
            {c.name: c for c in self.children.values() if c.parent_path == parent_path}
        )


def model_identity(model: Any) -> str:
    if isinstance(model, str):
        return model
    name = getattr(model, "model_name", None) or getattr(model, "model", None) or ""
    return f"{type(model).__name__}:{name}"


def permission_identity(permissions: Sequence[Any] | None) -> tuple[str, ...]:
    return tuple(repr(item) for item in permissions or ())


def _reject_reserved(names: Sequence[str]) -> None:
    if TASK_TOOL_NAME in names:
        raise PreparedChildCatalogError("child_tool_reserved")


def _tool_names(tools: Sequence[Any]) -> tuple[str, ...]:
    names = [getattr(tool, "name", None) for tool in tools]
    if any(not isinstance(name, str) for name in names):
        raise PreparedChildCatalogError("child_tool_unidentified")
    result = tuple(sorted(name for name in names if isinstance(name, str)))
    _reject_reserved(result)
    return result


def _graph_tool_names(graph: Any) -> set[str]:
    nodes = getattr(graph, "nodes", None)
    if not isinstance(nodes, Mapping):
        raise PreparedChildCatalogError("trusted_child_unverifiable")
    names: set[str] = set()
    for node_name, node in nodes.items():
        tools_by_name = getattr(getattr(node, "bound", None), "tools_by_name", None)
        if node_name == "tools" and not isinstance(tools_by_name, Mapping):
            raise PreparedChildCatalogError("trusted_child_unverifiable")
        if isinstance(tools_by_name, Mapping):
            names.update(tools_by_name)
    return names


def _compiled_tool_names(runnable: Any) -> tuple[str, ...]:
    """Enumerate a compiled child's tools; fail closed on an opaque or task-capable graph."""

    names = _graph_tool_names(runnable)
    get_subgraphs = getattr(runnable, "get_subgraphs", None)
    if not callable(get_subgraphs):
        raise PreparedChildCatalogError("trusted_child_unverifiable")
    nested: set[str] = set()
    subgraphs: Any = get_subgraphs(recurse=True)
    for _namespace, subgraph in subgraphs:
        nested.update(_graph_tool_names(subgraph))
    _reject_reserved([*names, *nested])
    return tuple(sorted(names))


class _Builder:
    def __init__(
        self,
        policy: Mapping[str, PreparedChildPolicy],
        trusted: Mapping[str, TrustedCompiledChild],
    ) -> None:
        self._policy = policy
        self._trusted = trusted
        self.children: dict[str, PreparedChild] = {}

    def level(self, specs: Sequence[Mapping[str, Any]], parent_path: str, inherit: dict) -> None:
        names = [spec.get("name") for spec in specs]
        valid = all(isinstance(n, str) and _CHILD_NAME.fullmatch(n) for n in names)
        if len(set(names)) != len(names) or not valid:
            raise PreparedChildCatalogError("child_name_invalid")
        if parent_path == ROOT_PATH and GENERAL_PURPOSE_NAME not in names:
            specs = [{**GENERAL_PURPOSE_SUBAGENT, **inherit, "_moldy_origin": "gp"}, *specs]
        for spec in specs:
            if "graph_id" in spec:
                raise PreparedChildCatalogError("async_child_unsupported")
            self.child(spec, parent_path, inherit)

    def child(self, spec: Mapping[str, Any], parent_path: str, inherit: dict) -> None:
        child_id = f"{parent_path}/{spec['name']}"
        if child_id in self.children:
            raise PreparedChildCatalogError("child_name_invalid")
        implicit = spec.get("_moldy_origin") == "gp"
        policy = self._policy.get(child_id)
        if policy is None and not implicit:
            raise PreparedChildCatalogError("child_not_in_policy")
        origin: ChildOrigin = "general_purpose" if implicit else "declarative"
        if "runnable" in spec:
            origin, model, tools, permissions = (
                "trusted_compiled",
                *self._trusted_identity(spec, child_id),
            )
        else:
            model = model_identity(spec.get("model", inherit["model"]))
            tools = _tool_names(spec.get("tools", inherit["tools"]))
            permissions = permission_identity(spec.get("permissions", inherit["permissions"]))
        if policy is not None and (
            policy.model != model
            or not set(tools) <= policy.tools
            or policy.permissions != permissions
        ):
            raise PreparedChildCatalogError("child_policy_mismatch")
        base_format = spec.get("response_format")
        response_variant_id(base_format)
        formats = [*spec.get(RESPONSE_VARIANTS_KEY, ())]
        formats += list(policy.response_formats if policy is not None else ())
        variant_values = {DEFAULT_VARIANT: base_format}
        variant_values.update({response_variant_id(value): value for value in formats})
        if origin == "trusted_compiled" and set(variant_values) != {DEFAULT_VARIANT}:
            raise PreparedChildCatalogError("compiled_child_variant_unsupported")
        nested = list(spec.get(NESTED_CHILDREN_KEY, ()))
        if nested:
            if origin == "trusted_compiled":
                raise PreparedChildCatalogError("trusted_child_unverifiable")
            child_inherit = {
                "model": spec.get("model", inherit["model"]),
                "tools": spec.get("tools", inherit["tools"]),
                "permissions": spec.get("permissions", inherit["permissions"]),
            }
            self.level(nested, child_id, child_inherit)
        clean = {k: v for k, v in spec.items() if k not in _CATALOG_KEYS}
        if origin != "trusted_compiled":
            clean = {**inherit, **clean}
            clean.setdefault("system_prompt", DEFAULT_SUBAGENT_PROMPT)
        record = {
            "id": child_id,
            "origin": origin,
            "description": spec.get("description", ""),
            "model": model,
            "tools": list(tools),
            "permissions": list(permissions),
            "variants": sorted(variant_values),
            "children": [f"{child_id}/{item['name']}" for item in nested],
            "actor": None if spec.get(ACTOR_ID_KEY) is None else str(spec[ACTOR_ID_KEY]),
        }
        self.children[child_id] = PreparedChild(
            child_id=child_id,
            parent_path=parent_path,
            name=str(spec["name"]),
            description=str(record["description"]),
            origin=origin,
            model=model,
            tools=tools,
            permissions=permissions,
            variants=tuple(record["variants"]),
            children=tuple(record["children"]),
            token=_sha(record),
            spec=MappingProxyType(clean),
            variant_values=MappingProxyType(variant_values),
        )

    def _trusted_identity(
        self, spec: Mapping[str, Any], child_id: str
    ) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
        trusted = self._trusted.get(child_id)
        if trusted is None or trusted.runnable is not spec["runnable"]:
            raise PreparedChildCatalogError("compiled_child_untrusted")
        _reject_reserved(sorted(trusted.tools))
        tools = _compiled_tool_names(trusted.runnable)
        if not set(tools) <= trusted.tools:
            raise PreparedChildCatalogError("child_policy_mismatch")
        return trusted.model, tuple(sorted(trusted.tools)), trusted.permissions


def build_prepared_child_catalog(
    subagents: Sequence[Mapping[str, Any]] | None,
    *,
    model: Any,
    tools: Sequence[Any],
    permissions: Sequence[Any] | None,
    stored_policy: Mapping[str, PreparedChildPolicy],
    trusted_compiled: Mapping[str, TrustedCompiledChild] | None = None,
) -> PreparedChildCatalog:
    """Normalize the effective child closure into one immutable catalog."""

    builder = _Builder(stored_policy, trusted_compiled or {})
    inherit = {"model": model, "tools": list(tools), "permissions": permissions}
    builder.level(list(subagents or ()), ROOT_PATH, inherit)
    children = dict(sorted(builder.children.items()))
    digest = _sha({key: child.token for key, child in children.items()})
    return PreparedChildCatalog(children=MappingProxyType(children), digest=digest)
