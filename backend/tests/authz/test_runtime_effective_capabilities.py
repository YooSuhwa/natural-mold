"""The effective capability census is closed over the installed compiled surface."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool, tool

from app.agent_runtime.lease_snapshot_backend import LeaseSnapshotBackend
from app.agent_runtime.prepared_run_digest import compute_prepared_run_digest
from app.agent_runtime.runtime_effective_capabilities import (
    CapabilityCensusError,
    ProviderOrigin,
    build_effective_census,
    lease_descriptor_ids,
    middleware_tool_names,
    tool_node_names,
)
from app.agent_runtime.runtime_snapshot_catalog import SnapshotCatalogBuilder
from app.agent_runtime.tool_access_policy import ToolOrigin
from tests.authz import prepared_run_testkit as kit

INSTALLED_GENERATED = {"ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep"}


class _Model(FakeMessagesListChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> _Model:
        return self


@tool
def web_search(query: str) -> str:
    """Search the web."""
    return query


@tool
def lookup_mcp(query: str) -> str:
    """MCP lookup."""
    return query


@tool
def update_document(document_id: str, text: str) -> str:
    """Live app tool."""
    return document_id


SELECTED: tuple[BaseTool, ...] = (web_search, lookup_mcp, update_document)
ORIGINS = (
    ToolOrigin("web_search", "builtin", "builtin:web_search"),
    ToolOrigin("lookup_mcp", "mcp", "server-1:lookup"),
    ToolOrigin(
        "update_document",
        "app",
        "documents.update",
        access_class="live_application_resource",
        resource_arg="document_id",
    ),
)
PROVIDERS = (
    ProviderOrigin("primary", "openai", "gpt-x"),
    ProviderOrigin("fallback:0", "anthropic", "claude-x"),
    ProviderOrigin("summary", "openai", "gpt-x"),
)


EXPECTED_TOOL_ACCESS = {
    **{f"root/{n}": "snapshot_safe" for n in ("ls", "read_file", "glob", "grep")},
    **{f"root/{n}": "run_output" for n in ("write_file", "edit_file", "delete")},
    "root/task": "live_application_resource",
    "root/web_search": "resolved_external",
    "root/lookup_mcp": "resolved_external",
    "root/update_document": "live_application_resource",
}


def _census(names, origins=ORIGINS, providers=PROVIDERS, **kwargs: Any):
    middleware = kwargs.pop("middleware", [TodoListMiddleware()])
    return build_effective_census(
        "root",
        effective_tool_names=names,
        selected_tools=origins,
        middleware=middleware,
        backends=kwargs.pop("backends", {}),
        providers=providers,
        **kwargs,
    )


def _lease_backend(tmp_path: Path) -> LeaseSnapshotBackend:
    run_root = tmp_path / "runs" / "run-1"
    (run_root / "output").mkdir(parents=True)
    return LeaseSnapshotBackend(
        SnapshotCatalogBuilder().build(),
        run_root=run_root,
        writable_roots={"/run/output": run_root / "output"},
        forbidden_root=tmp_path / "data",
    )


def test_installed_compiler_surface_gets_one_descriptor_per_effective_item(
    tmp_path: Path,
) -> None:
    graph = create_deep_agent(
        model=_Model(responses=[AIMessage(content="done")]),
        tools=list(SELECTED),
        backend=StateBackend(),
    )
    names = tool_node_names(graph)
    assert INSTALLED_GENERATED | {"execute", "task"} <= set(names)

    census = _census(
        names,
        backends={"root": _lease_backend(tmp_path)},
        preloaded_resources={"update_document": ["doc-1"]},
    )

    tools = census.tool_descriptors
    assert set(tools) == set(names) - {"execute"}
    assert census.denied_tool_names == frozenset({"execute"})
    assert [d.name for d in census.descriptors if d.kind == "tool"].count("task") == 1
    assert {tools[n].access_class for n in ("ls", "read_file", "glob", "grep")} == {"snapshot_safe"}
    assert {tools[n].access_class for n in ("write_file", "edit_file", "delete")} == {"run_output"}
    assert tools["task"].operation_kind == "orchestration_admission"
    assert tools["web_search"].access_class == "resolved_external"
    assert tools["lookup_mcp"].access_class == "resolved_external"
    assert tools["update_document"].resource_arg == "document_id"
    kinds = [d.kind for d in census.descriptors]
    assert kinds.count("provider") == 3 and kinds.count("backend") == 1
    assert kinds.count("middleware") == 1
    assert len(census.descriptor_ids) == len(census.descriptors)


def test_census_is_stable_and_feeds_lease_digest_material() -> None:
    names = [*INSTALLED_GENERATED, "task", "execute", "web_search", "lookup_mcp"]
    names.append("update_document")
    first = _census(names)
    second = _census(list(reversed(names)))
    assert first.descriptor_ids == second.descriptor_ids
    assert first.digest() == second.digest()
    assert first.digest() != _census(names, preloaded_resources={"update_document": ["d"]}).digest()

    assert first.tool_access() == EXPECTED_TOOL_ACCESS
    material = kit.material(kit.binding(), tool_access=first.tool_access())
    oracle = kit.material(kit.binding(), tool_access=dict(EXPECTED_TOOL_ACCESS))
    assert compute_prepared_run_digest(material) == compute_prepared_run_digest(oracle)
    widened = {**EXPECTED_TOOL_ACCESS, "root/read_file": "run_output"}
    widened_material = kit.material(kit.binding(), tool_access=widened)
    assert compute_prepared_run_digest(material) != compute_prepared_run_digest(widened_material)
    child = build_effective_census(
        "root/child",
        effective_tool_names=["read_file"],
        selected_tools=(),
        middleware=[],
        backends={},
        providers=(ProviderOrigin("primary", "google", "gemini-x"),),
    )
    union = lease_descriptor_ids([first, child])
    assert "tool:root/child:generated:read_file" in union
    assert first.descriptor_ids | child.descriptor_ids == union


def test_middleware_generated_tool_names_are_enumerated_from_instances() -> None:
    middleware = [FilesystemMiddleware(backend=StateBackend()), TodoListMiddleware()]
    names = middleware_tool_names(middleware)
    assert set(names) == INSTALLED_GENERATED | {"execute", "write_todos"}
    census = _census(names, origins=(), middleware=middleware)
    assert census.tool_descriptors["write_todos"].access_class == "run_output"


@pytest.mark.parametrize(
    ("names", "origins", "code"),
    [
        (["read_file", "start_async_task"], (), "capability_tool_unknown"),
        (["mystery"], (), "capability_tool_unknown"),
        (
            ["read_file"],
            (ToolOrigin("read_file", "mcp", "srv:read_file"),),
            "capability_tool_shadowed",
        ),
        (["execute"], (ToolOrigin("execute", "registry", "x"),), "capability_tool_shadowed"),
        (
            ["execute_in_skill"],
            (ToolOrigin("execute_in_skill", "mcp", "srv:x"),),
            "capability_tool_shadowed",
        ),
        (
            ["compact_conversation"],
            (ToolOrigin("compact_conversation", "skill", "compact_conversation"),),
            "capability_tool_shadowed",
        ),
        (["x"], (ToolOrigin("x", "skill", "other_skill_tool"),), "tool_skill_unknown"),
        (["x"], (ToolOrigin("x", "builtin", "builtin:unknown"),), "tool_builtin_unknown"),
        (["x"], (ToolOrigin("x", "app", "a"),), "tool_access_undeclared"),
        (
            ["x"],
            (ToolOrigin("x", "app", "a", access_class="live_application_resource"),),
            "tool_resource_arg_missing",
        ),
        (
            ["x"],
            (ToolOrigin("x", "mcp", "s", access_class="snapshot_safe"),),
            "tool_access_override_forbidden",
        ),
        (["x"], (ToolOrigin("x", "plugin", "p"),), "tool_source_unknown"),  # type: ignore[arg-type]
        ([], (ToolOrigin("ghost", "mcp", "s"),), "capability_tool_not_effective"),
        (["ls", "ls"], (), "capability_tool_duplicate"),
    ],
)
def test_unknown_or_unclassifiable_tools_reject_before_marker(names, origins, code) -> None:
    with pytest.raises(CapabilityCensusError) as rejected:
        _census(names, origins=origins)
    assert rejected.value.code == code


def test_unsupported_provider_and_stray_resource_sets_reject() -> None:
    with pytest.raises(CapabilityCensusError) as provider:
        _census(["ls"], origins=(), providers=(ProviderOrigin("primary", "ollama", "m"),))
    assert provider.value.code == "capability_provider_unsupported"
    with pytest.raises(CapabilityCensusError) as stray:
        _census(["ls"], origins=(), preloaded_resources={"ls": ["x"]})
    assert stray.value.code == "capability_resource_tool_unknown"


class _ForeignSnapshotBackend(LeaseSnapshotBackend):
    pass


def test_backends_must_be_exactly_the_lease_snapshot_backend(tmp_path: Path) -> None:
    accepted = _census(["ls"], origins=(), backends={"root": _lease_backend(tmp_path)})
    assert [d.source for d in accepted.descriptors if d.kind == "backend"] == [
        "app.agent_runtime.lease_snapshot_backend.LeaseSnapshotBackend"
    ]
    subclass = object.__new__(_ForeignSnapshotBackend)
    for backend in (StateBackend(), subclass, object()):
        with pytest.raises(CapabilityCensusError) as rejected:
            _census(["ls"], origins=(), backends={"root": backend})
        assert rejected.value.code == "capability_backend_unsupported"
