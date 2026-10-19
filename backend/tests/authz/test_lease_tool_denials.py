"""Known never-dispatchable tools and lease-bound descriptor sets (I12c review fixes)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from app.agent_runtime import lease_bound_tool
from app.agent_runtime.lease_bound_tool import LeaseBoundToolMiddleware
from app.agent_runtime.runtime_effective_capabilities import build_effective_census
from app.agent_runtime.tool_access_policy import ToolAccessPolicyError, ToolOrigin, classify_tool
from tests.authz import prepared_run_testkit as kit

ENTERED: list[str] = []


class _Model(FakeMessagesListChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> _Model:
        return self


@tool
def execute_in_skill(skill_directory: str, command: str) -> str:
    """Live skill subprocess."""
    ENTERED.append("execute_in_skill")
    return "ran"


@tool
def compact_conversation() -> str:
    """Provider send inside a tool handler."""
    ENTERED.append("compact_conversation")
    return "compacted"


SKILL_ORIGIN = ToolOrigin("execute_in_skill", "skill", "execute_in_skill")
CENSUS = build_effective_census(
    "root",
    effective_tool_names=["execute_in_skill", "compact_conversation", "read_file"],
    selected_tools=(SKILL_ORIGIN,),
    middleware=[],
    backends={},
    providers=(),
)


def _lease(**overrides: Any):
    values: dict[str, Any] = {
        "descriptor_ids": CENSUS.descriptor_ids,
        "tool_access": CENSUS.tool_access(),
    }
    values.update(overrides)
    return replace(kit.lease(kit.binding(), "token"), **values)


def _no_session() -> Any:
    raise AssertionError("a denied tool must not open a permit session")


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch):
    ENTERED.clear()

    def no_permit(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a denied tool must not open a dispatch permit")

    monkeypatch.setattr(lease_bound_tool, "open_dispatch_permit", no_permit)


def test_live_skill_execution_and_compaction_are_denied_in_the_census() -> None:
    assert CENSUS.denied_tool_names == frozenset({"execute_in_skill", "compact_conversation"})
    assert set(CENSUS.tool_descriptors) == {"read_file"}
    assert CENSUS.tool_access() == {"root/read_file": "snapshot_safe"}
    with pytest.raises(ToolAccessPolicyError) as skill:
        classify_tool(SKILL_ORIGIN)
    assert skill.value.code == "skill_execution_not_snapshotted"
    with pytest.raises(ToolAccessPolicyError) as compact:
        classify_tool(ToolOrigin("compact_conversation", "generated", "compact_conversation"))
    assert compact.value.code == "compaction_not_leased"


@pytest.mark.asyncio
async def test_guard_denies_denied_tools_before_permit_and_handler() -> None:
    guard = LeaseBoundToolMiddleware(_lease(), CENSUS, session_factory=_no_session)
    calls = [
        {"name": "execute_in_skill", "args": {"skill_directory": "s", "command": "c"}, "id": "c1"},
        {"name": "compact_conversation", "args": {}, "id": "c2"},
    ]
    model = _Model(responses=[AIMessage(content="", tool_calls=calls), AIMessage(content="ok")])
    agent = create_agent(
        model=model, tools=[execute_in_skill, compact_conversation], middleware=[guard]
    )

    result = await agent.ainvoke(cast(Any, {"messages": [HumanMessage("go")]}))

    by_id = {
        m.tool_call_id: (m.status, str(m.content))
        for m in result["messages"]
        if isinstance(m, ToolMessage)
    }
    assert by_id == {
        "c1": ("error", "Tool call denied: skill_execution_not_snapshotted"),
        "c2": ("error", "Tool call denied: compaction_not_leased"),
    }
    assert ENTERED == []
    assert result["messages"][-1].content == "ok"


def test_guard_requires_census_inside_the_lease_descriptor_and_access_sets() -> None:
    guard = LeaseBoundToolMiddleware(_lease(), CENSUS, session_factory=_no_session)
    assert guard.name == "LeaseBoundToolMiddleware[root]"
    with pytest.raises(ValueError, match="descriptor"):
        LeaseBoundToolMiddleware(
            _lease(descriptor_ids=frozenset()), CENSUS, session_factory=_no_session
        )
    widened = {"root/read_file": "run_output"}
    with pytest.raises(ValueError, match="tool access"):
        LeaseBoundToolMiddleware(_lease(tool_access=widened), CENSUS, session_factory=_no_session)
    # An empty lease tool_access map is not compared (legacy leases); descriptors still are.
    LeaseBoundToolMiddleware(_lease(tool_access={}), CENSUS, session_factory=_no_session)
