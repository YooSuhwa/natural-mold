"""The prepared child closure is recursive, policy-verified, immutable and digested."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, TypedDict

import pytest
from deepagents.backends import StateBackend
from deepagents.middleware.filesystem import FilesystemPermission
from deepagents.middleware.subagents import SubAgentMiddleware
from langchain.agents import create_agent
from langchain.agents.structured_output import AutoStrategy, ProviderStrategy, ToolStrategy
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.graph import START, MessagesState, StateGraph
from pydantic import BaseModel

from app.agent_runtime.prepared_subagent_catalog import (
    DEFAULT_VARIANT,
    PreparedChildCatalogError,
    PreparedChildPolicy,
    TrustedCompiledChild,
    build_prepared_child_catalog,
    model_identity,
    permission_identity,
    response_variant_id,
)
from tests.authz.runtime_prototype import ScriptedModel


class Findings(BaseModel):
    summary: str


@dataclass
class Point:
    x: int


class Shape(TypedDict):
    sides: int


@tool
def search(query: str) -> str:
    """Search."""
    return query


@tool
def task(description: str) -> str:
    """Shadow task."""
    return description


@tool
def shell(command: str) -> str:
    """Run."""
    return command


MODEL = ScriptedModel(responses=[AIMessage(content="x")])
MODEL_ID = model_identity(MODEL)
PERMS = [FilesystemPermission(operations=["read"], paths=["/conversations/**"])]


def _policy(**overrides: Any) -> dict[str, PreparedChildPolicy]:
    base = PreparedChildPolicy(MODEL_ID, frozenset({"search"}), permission_identity(PERMS))
    policy = {"root/researcher": base, "root/researcher/checker": base}
    policy.update(overrides)
    return policy


def _spec(name: str = "researcher") -> dict[str, Any]:
    return {"name": name, "description": f"{name} role", "system_prompt": "p", "tools": [search]}


def _catalog(specs: list[dict[str, Any]], **kwargs: Any):
    kwargs.setdefault("stored_policy", _policy())
    return build_prepared_child_catalog(
        specs, model=MODEL, tools=[search], permissions=PERMS, **kwargs
    )


def test_variant_ids_are_stable_canonical_forms() -> None:
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
    reordered = {"required": ["a"], "properties": {"a": {"type": "string"}}, "type": "object"}
    assert response_variant_id(None) == DEFAULT_VARIANT
    assert response_variant_id(schema) == response_variant_id(reordered)
    assert response_variant_id(schema).startswith("dict:")
    assert response_variant_id(Findings) == response_variant_id(Findings)
    assert response_variant_id(Findings).startswith("type:")
    assert response_variant_id(Point) != response_variant_id(Shape)
    tool_strategy = response_variant_id(ToolStrategy(Findings))
    assert tool_strategy.startswith(f"schema:ToolStrategy:{response_variant_id(Findings)}:")
    assert response_variant_id(AutoStrategy(schema)) != tool_strategy


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (ToolStrategy(Findings), ToolStrategy(Findings, tool_message_content="done")),
        (ToolStrategy(Findings), ToolStrategy(Findings, handle_errors=False)),
        (ToolStrategy(Findings), ToolStrategy(Findings, handle_errors="retry")),
        (ToolStrategy(Findings), ToolStrategy(Findings, handle_errors=ValueError)),
        (ProviderStrategy(Findings, strict=True), ProviderStrategy(Findings, strict=False)),
    ],
)
def test_variant_ids_cover_strategy_fields(first: Any, second: Any) -> None:
    assert response_variant_id(first) != response_variant_id(second)
    assert response_variant_id(first) == response_variant_id(first)


_CALLABLE_HANDLER: Any = print


async def _async_format() -> type:
    return Findings


@pytest.mark.parametrize(
    "value",
    [
        lambda: Findings,
        _async_format,
        object(),
        "token",
        int,
        ToolStrategy(Findings, handle_errors=_CALLABLE_HANDLER),
    ],
)
def test_callable_async_and_unknown_variants_are_rejected(value: Any) -> None:
    with pytest.raises(PreparedChildCatalogError) as rejected:
        response_variant_id(value)
    assert rejected.value.code == "response_variant_unsupported"
    with pytest.raises(PreparedChildCatalogError):
        _catalog([{**_spec(), "_moldy_response_variants": [value]}])


def test_no_linked_children_yields_the_implicit_general_purpose_child() -> None:
    catalog = _catalog([])
    (child,) = catalog.children.values()
    assert child.child_id == "root/general-purpose"
    assert child.origin == "general_purpose"
    assert child.tools == ("search",)
    assert child.variants == (DEFAULT_VARIANT,)
    assert child.spec["model"] is MODEL


def test_nested_children_get_stable_ids_tokens_and_digest() -> None:
    nested = {**_spec(), "subagents": [_spec("checker")]}
    first = _catalog([nested])
    again = _catalog([{**_spec(), "subagents": [_spec("checker")]}])
    assert sorted(first.children) == [
        "root/general-purpose",
        "root/researcher",
        "root/researcher/checker",
    ]
    assert first.children["root/researcher"].children == ("root/researcher/checker",)
    assert set(first.level("root/researcher")) == {"checker"}
    assert "subagents" not in first.children["root/researcher"].spec
    assert first.digest == again.digest
    variant = {**nested, "_moldy_response_variants": [Findings]}
    assert _catalog([variant]).digest != first.digest
    with pytest.raises(TypeError):
        first.children["root/x"] = first.children["root/researcher"]  # type: ignore[index]


@pytest.mark.parametrize(
    ("spec", "policy", "code"),
    [
        (_spec("intruder"), None, "child_not_in_policy"),
        ({**_spec(), "tools": [search, shell]}, None, "child_policy_mismatch"),
        ({**_spec(), "permissions": []}, None, "child_policy_mismatch"),
        (
            _spec(),
            {"root/researcher": PreparedChildPolicy("other:model", frozenset({"search"}))},
            "child_policy_mismatch",
        ),
        ({**_spec(), "graph_id": "remote"}, None, "async_child_unsupported"),
        (_spec("a/b"), None, "child_name_invalid"),
        (_spec("bad name"), None, "child_name_invalid"),
        ({**_spec(), "tools": [search, task]}, None, "child_tool_reserved"),
    ],
)
def test_wrong_child_tools_permission_or_model_fail_before_marker(
    spec: dict[str, Any], policy: dict[str, PreparedChildPolicy] | None, code: str
) -> None:
    with pytest.raises(PreparedChildCatalogError) as rejected:
        _catalog([spec], stored_policy=policy or _policy())
    assert rejected.value.code == code


def test_trusted_compiled_child_is_bound_by_identity_and_verified() -> None:
    runnable = create_agent(MODEL, tools=[search])
    trusted = {
        "root/researcher": TrustedCompiledChild(
            runnable, MODEL_ID, frozenset({"search"}), permission_identity(PERMS)
        )
    }
    entry = {"name": "researcher", "description": "compiled", "runnable": runnable}
    catalog = _catalog([entry], trusted_compiled=trusted)
    child = catalog.children["root/researcher"]
    assert (child.origin, child.model, child.tools) == ("trusted_compiled", MODEL_ID, ("search",))

    impostor = {**entry, "runnable": create_agent(MODEL, tools=[search])}
    with pytest.raises(PreparedChildCatalogError) as untrusted:
        _catalog([impostor], trusted_compiled=trusted)
    assert untrusted.value.code == "compiled_child_untrusted"

    wide = create_agent(MODEL, tools=[search, shell])
    widened = {"root/researcher": TrustedCompiledChild(wide, MODEL_ID, frozenset({"search"}))}
    with pytest.raises(PreparedChildCatalogError) as mismatch:
        _catalog([{**entry, "runnable": wide}], trusted_compiled=widened)
    assert mismatch.value.code == "child_policy_mismatch"

    with pytest.raises(PreparedChildCatalogError) as variant:
        _catalog([{**entry, "_moldy_response_variants": [Findings]}], trusted_compiled=trusted)
    assert variant.value.code == "compiled_child_variant_unsupported"


def _trusted_rejection(runnable: Any, tools: frozenset[str] = frozenset({"search"})) -> str:
    trusted = {"root/researcher": TrustedCompiledChild(runnable, MODEL_ID, tools)}
    entry = {"name": "researcher", "description": "compiled", "runnable": runnable}
    with pytest.raises(PreparedChildCatalogError) as rejected:
        _catalog([entry], trusted_compiled=trusted)
    return rejected.value.code


def test_trusted_compiled_child_cannot_hide_a_task_handler() -> None:
    plain = create_agent(MODEL, tools=[search])
    assert _trusted_rejection(plain, frozenset({"search", "task"})) == "child_tool_reserved"
    shadow = create_agent(MODEL, tools=[search, task])
    assert _trusted_rejection(shadow, frozenset({"search", "task"})) == "child_tool_reserved"
    inner = {"name": "inner", "description": "i", "runnable": plain}
    stocked = create_agent(
        MODEL,
        tools=[search],
        middleware=[SubAgentMiddleware(backend=StateBackend(), subagents=[inner])],  # type: ignore[list-item]
    )
    assert _trusted_rejection(stocked, frozenset({"search", "task"})) == "child_tool_reserved"
    nested = StateGraph(MessagesState)
    nested.add_node("inner", create_agent(MODEL, tools=[task]))
    nested.add_edge(START, "inner")
    assert _trusted_rejection(nested.compile()) == "child_tool_reserved"
    opaque = SimpleNamespace(nodes={"tools": SimpleNamespace(bound=None)})
    assert _trusted_rejection(opaque) == "trusted_child_unverifiable"
