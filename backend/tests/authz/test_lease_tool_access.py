"""The generic lease leaf guard denies before handler entry and permits each leaf."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from deepagents import create_deep_agent
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import lease_bound_tool
from app.agent_runtime.lease_bound_tool import LeaseBoundToolMiddleware
from app.agent_runtime.lease_dispatch_permit import DispatchDeniedError
from app.agent_runtime.lease_snapshot_backend import LeaseSnapshotBackend
from app.agent_runtime.prepared_run_registry import (
    PreparedRunRegistry,
    reset_prepared_run_registry_for_tests,
)
from app.agent_runtime.runtime_config import PreparedRunBinding
from app.agent_runtime.runtime_effective_capabilities import (
    EffectiveCapabilityCensus,
    build_effective_census,
    tool_node_names,
)
from app.agent_runtime.runtime_snapshot_catalog import SnapshotCatalogBuilder
from app.agent_runtime.tool_access_policy import ToolOrigin
from app.models.organization import Organization, OrganizationMember
from tests.authz import prepared_run_testkit as kit
from tests.authz.execution_gateway_testkit import GatewayGraph, seed_graph
from tests.conftest import TestSession

CALLS: list[str] = []
PERMITS = {"open": 0, "entered": 0}


class _Model(FakeMessagesListChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> _Model:
        return self


@tool
def update_document(document_id: str | list[str], text: str) -> str:
    """Live application tool."""
    CALLS.append(f"update:{document_id}:in_permit={PERMITS['open'] >= 1}")
    return "updated"


@tool
def web_search(query: str) -> str:
    """External search."""
    CALLS.append(f"search:in_permit={PERMITS['open'] >= 1}")
    return "results"


@tool
def nested_send(query: str) -> str:
    """A handler whose own nested permit is refused."""
    CALLS.append(f"nested:in_permit={PERMITS['open'] >= 1}")
    raise DispatchDeniedError("nested_permit_denied")


ORIGINS = (
    ToolOrigin("web_search", "builtin", "builtin:web_search"),
    ToolOrigin("nested_send", "registry", "nested.send"),
    ToolOrigin(
        "update_document",
        "app",
        "documents.update",
        access_class="live_application_resource",
        resource_arg="document_id",
    ),
)


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch):
    CALLS.clear()
    PERMITS.update(open=0, entered=0)
    real = lease_bound_tool.open_dispatch_permit

    @asynccontextmanager
    async def counting(*args: Any, **kwargs: Any):
        async with real(*args, **kwargs) as permit:
            PERMITS["open"] += 1
            PERMITS["entered"] += 1
            try:
                yield permit
            finally:
                PERMITS["open"] -= 1

    monkeypatch.setattr(lease_bound_tool, "open_dispatch_permit", counting)
    yield
    reset_prepared_run_registry_for_tests()


CENSUS = build_effective_census(
    "root",
    effective_tool_names=["update_document", "web_search", "nested_send"],
    selected_tools=ORIGINS,
    middleware=[],
    backends={},
    providers=(),
    preloaded_resources={"update_document": ["doc-1", "doc-2"]},
)


async def _active_lease(graph: GatewayGraph, census: EffectiveCapabilityCensus = CENSUS):
    registry = PreparedRunRegistry()
    reset_prepared_run_registry_for_tests(registry)
    ctx = graph.context
    binding = PreparedRunBinding(
        ctx.tenant_id,
        ctx.org_id,
        ctx.user_id,
        graph.agent_id,
        graph.conversation_ids[0],
        uuid.uuid4(),
        "thread",
    )
    token = registry.reserve(binding)
    lease = replace(
        kit.lease(binding, token),
        descriptor_ids=census.descriptor_ids,
        tool_access=census.tool_access(),
    )
    await registry.stage(lease)
    registry.activate(binding.run_id, token=token, digest=lease.digest)
    return lease


def _call(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


async def _run(lease, calls: list[dict[str, Any]]) -> tuple[list[ToolMessage], Any]:
    guard = LeaseBoundToolMiddleware(lease, CENSUS, session_factory=TestSession)
    model = _Model(responses=[AIMessage(content="", tool_calls=calls), AIMessage(content="ok")])
    tools = [update_document, web_search, nested_send]
    agent = create_agent(model=model, tools=tools, middleware=[guard])
    result = await agent.ainvoke(cast(Any, {"messages": [HumanMessage("go")]}))
    messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert result["messages"][-1].content == "ok"  # graph reaches a bounded terminal state
    return messages, guard


async def _archive(db: AsyncSession, graph: GatewayGraph) -> None:
    organization = await db.get(Organization, graph.context.org_id)
    assert organization is not None
    organization.status, organization.archived_at = "archived", datetime.now(UTC)
    await db.commit()


@pytest.mark.asyncio
async def test_denies_live_unknown_and_unprepared_dynamic_resources(db: AsyncSession) -> None:
    graph = await seed_graph(db)
    lease = await _active_lease(graph)
    calls = [
        _call("update_document", {"document_id": "doc-1", "text": "a"}, "call-ok"),
        _call("update_document", {"document_id": "doc-new", "text": "b"}, "call-new"),
        _call("update_document", {"document_id": ["doc-2", "doc-x"], "text": "c"}, "call-mix"),
        _call("update_document", {"text": "no id"}, "call-noid"),
        _call("mystery_tool", {}, "call-unknown"),
        _call("web_search", {"query": "q"}, "call-search"),
    ]

    messages, guard = await _run(lease, calls)

    by_id = {m.tool_call_id: m for m in messages}
    assert by_id["call-ok"].content == "updated"
    assert by_id["call-search"].content == "results"
    for call_id in ("call-new", "call-mix", "call-noid"):
        assert by_id[call_id].status == "error"
        assert "live_resource_not_prepared" in str(by_id[call_id].content)
    assert "descriptor_not_prepared" in str(by_id["call-unknown"].content)
    assert sorted(CALLS) == ["search:in_permit=True", "update:doc-1:in_permit=True"]
    assert PERMITS == {"open": 0, "entered": 5}  # unknown name never reaches SQL
    assert {d.tool_call_id for d in guard.denials} == {
        "call-new",
        "call-mix",
        "call-noid",
        "call-unknown",
    }


@pytest.mark.asyncio
async def test_archived_org_allows_only_preloaded_resources(db: AsyncSession) -> None:
    graph = await seed_graph(db)
    lease = await _active_lease(graph)
    await _archive(db, graph)
    calls = [
        _call("update_document", {"document_id": "doc-2", "text": "a"}, "call-pre"),
        _call("update_document", {"document_id": "doc-new", "text": "b"}, "call-new"),
    ]

    messages, _guard = await _run(lease, calls)

    by_id = {m.tool_call_id: m for m in messages}
    assert by_id["call-pre"].content == "updated"
    assert "organization_archived" in str(by_id["call-new"].content)
    assert by_id["call-new"].status == "error"
    assert CALLS == ["update:doc-2:in_permit=True"]


@pytest.mark.asyncio
async def test_revoked_membership_denies_every_leaf_before_handler(db: AsyncSession) -> None:
    graph = await seed_graph(db)
    lease = await _active_lease(graph)
    member = await db.get(OrganizationMember, (graph.context.org_id, graph.context.user_id))
    assert member is not None
    member.status = "removed"
    await db.commit()
    calls = [
        _call("web_search", {"query": "q"}, "call-search"),
        _call("update_document", {"document_id": "doc-1", "text": "a"}, "call-live"),
    ]

    messages, _guard = await _run(lease, calls)

    assert CALLS == []
    assert {m.tool_call_id: str(m.content) for m in messages} == {
        "call-search": "Tool call denied: forbidden",
        "call-live": "Tool call denied: forbidden",
    }


@pytest.mark.asyncio
async def test_installed_generated_tools_read_frozen_snapshot_through_the_guard(
    db: AsyncSession, tmp_path: Path
) -> None:
    graph = await seed_graph(db)
    builder = SnapshotCatalogBuilder()
    builder.add_bytes("/agents/a/AGENTS.md", b"frozen memory\n", kind="memory")
    output = tmp_path / "run" / "output"
    output.mkdir(parents=True)
    backend = LeaseSnapshotBackend(
        builder.build(),
        run_root=tmp_path / "run",
        writable_roots={"/run/output": output},
        forbidden_root=tmp_path / "data",
    )
    calls = [
        _call("read_file", {"file_path": "/agents/a/AGENTS.md"}, "call-read"),
        _call("write_file", {"file_path": "/agents/a/AGENTS.md", "content": "x"}, "call-ro"),
        _call("write_file", {"file_path": "/run/output/r.md", "content": "out"}, "call-out"),
        _call("execute", {"command": "cat /etc/passwd"}, "call-exec"),
        _call("task", {"description": "d", "subagent_type": "general-purpose"}, "call-task"),
    ]
    model = _Model(responses=[AIMessage(content="", tool_calls=calls), AIMessage(content="ok")])
    probe = create_deep_agent(model=model, backend=backend)
    census = build_effective_census(
        "root",
        effective_tool_names=tool_node_names(probe),
        selected_tools=(),
        middleware=[],
        backends={"root": backend},
        providers=(),
    )
    lease = await _active_lease(graph, census)
    guard = LeaseBoundToolMiddleware(lease, census, session_factory=TestSession)
    agent = create_deep_agent(model=model, backend=backend, middleware=[guard])

    result = await agent.ainvoke(cast(Any, {"messages": [HumanMessage("go")]}))

    by_id = {
        m.tool_call_id: str(m.content) for m in result["messages"] if isinstance(m, ToolMessage)
    }
    assert "frozen memory" in by_id["call-read"]
    assert "read-only" in by_id["call-ro"]
    assert (output / "r.md").read_text() == "out"
    assert by_id["call-exec"] == "Tool call denied: descriptor_not_prepared"
    assert by_id["call-task"] == "Tool call denied: live_resource_not_prepared"
    assert PERMITS == {"open": 0, "entered": 3}


@pytest.mark.asyncio
async def test_handler_raised_dispatch_denial_becomes_bounded_error(db: AsyncSession) -> None:
    graph = await seed_graph(db)
    lease = await _active_lease(graph)
    calls = [_call("nested_send", {"query": "q"}, "call-nested")]

    messages, guard = await _run(lease, calls)

    assert CALLS == ["nested:in_permit=True"]  # the handler ran inside its permit
    assert [(m.tool_call_id, m.status, str(m.content)) for m in messages] == [
        ("call-nested", "error", "Tool call denied: nested_permit_denied")
    ]
    assert [d.code for d in guard.denials] == ["nested_permit_denied"]
    assert PERMITS == {"open": 0, "entered": 1}
