"""Search authorization belongs to the server-bound run, even for customized tools."""

from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import tool_factory
from app.models.tool import Tool
from app.services.research_search_policy import ResearchSearchActor, SearchPolicyError
from app.tools.definitions.search_transport import ResearchSearchError
from app.tools.domain import ToolRunContext
from app.tools.registry import registry
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, ensure_unit_scope
from tests.conftest import TEST_USER_ID, override_get_current_user


@pytest.mark.parametrize("key", ["tavily_search", "research_search"])
@pytest.mark.asyncio
async def test_runtime_uses_trusted_scope_and_only_summarizes_provider_and_count(
    key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = uuid4()
    forged = str(uuid4())
    seen: list[ResearchSearchActor | None] = []
    provider = "tavily" if key == "tavily_search" else "searxng"

    async def observe(ctx: ToolRunContext) -> dict:
        seen.append(ctx.search_actor)
        return {
            "provider": provider,
            "query": ctx.parameters["query"],
            "results": [{"url": "https://source.example/private"}],
        }

    monkeypatch.setattr(registry.require(key), "runner", observe)
    post = AsyncMock()
    monkeypatch.setattr(tool_factory.hooks, "run_pre", AsyncMock())
    monkeypatch.setattr(tool_factory.hooks, "run_post", post)
    tool = tool_factory.create_tool_for_runtime(
        {
            "definition_key": key,
            "name": "search_friendly_name",
            "user_id": str(uuid4()),  # Fixed agent owner can differ from the caller.
            "scope_user_id": str(TEST_USER_ID),
            "org_id": str(TEST_ORG_ID),
            "tenant_id": str(TEST_TENANT_ID),
            "agent_id": str(agent_id),
            "parameters": {
                "org_id": forged,
                "tenant_id": forged,
                "scope_user_id": forged,
                "search_actor": {"user_id": forged},
            },
        }
    )
    assert tool is not None
    output = await tool.ainvoke({"query": "private query", "user_id": forged, "org_id": forged})
    assert seen == [ResearchSearchActor(TEST_TENANT_ID, TEST_ORG_ID, TEST_USER_ID, agent_id)]
    assert output["query"] == "private query"
    assert post.await_args is not None
    assert post.await_args.args[1].output == f"provider={provider} results=1"


@pytest.mark.asyncio
async def test_manual_execution_binds_the_http_callers_scope(
    client: httpx.AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await ensure_unit_scope(db, await override_get_current_user())
    forged = str(uuid4())
    tool = Tool(
        definition_key="research_search",
        user_id=TEST_USER_ID,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        name="research",
        parameters={"user_id": forged, "org_id": forged},
    )
    db.add(tool)
    await db.commit()
    seen: list[ResearchSearchActor | None] = []

    async def observe(ctx: ToolRunContext) -> dict:
        seen.append(ctx.search_actor)
        return {"results": []}

    monkeypatch.setattr(registry.require("research_search"), "runner", observe)
    response = await client.post(
        f"/api/tools/{tool.id}/run",
        json={
            "runtime_args": {
                "query": "scope test",
                "org_id": forged,
                "tenant_id": forged,
                "user_id": forged,
                "agent_id": forged,
                "search_actor": {"org_id": forged},
            }
        },
    )
    assert response.status_code == 200
    assert response.json()["success"], response.json()
    assert seen == [ResearchSearchActor(TEST_TENANT_ID, TEST_ORG_ID, TEST_USER_ID, None)]

    async def provider_denied(_ctx: ToolRunContext) -> dict:
        raise ResearchSearchError("Search provider denied access.", http_status=403)

    monkeypatch.setattr(registry.require("research_search"), "runner", provider_denied)
    denied = await client.post(
        f"/api/tools/{tool.id}/run", json={"runtime_args": {"query": "scope test"}}
    )
    assert denied.status_code == 200
    assert denied.json()["success"] is False
    assert denied.json()["http_status"] == 403
    assert denied.json()["error"] == "Search provider denied access."


@pytest.mark.parametrize(
    ("key", "error", "message_text"),
    [
        (
            "tavily_search",
            ResearchSearchError("Provider quota reached.", 429),
            "Provider quota reached.",
        ),
        (
            "research_search",
            SearchPolicyError("Research search is disabled."),
            "Research search is disabled.",
        ),
        (
            "research_search",
            RuntimeError("Bad upstream value for private query"),
            "Research search could not be completed. Retry later.",
        ),
    ],
)
@pytest.mark.asyncio
async def test_settled_search_error_is_returned_to_the_model_after_failure_hook(
    key: str, error: Exception, message_text: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    failure = AsyncMock()
    monkeypatch.setattr(tool_factory.hooks, "run_pre", AsyncMock())
    monkeypatch.setattr(tool_factory.hooks, "run_failure", failure)
    runner = AsyncMock(side_effect=error)
    monkeypatch.setattr(registry.require(key), "runner", runner)
    tool = tool_factory.create_tool_for_runtime(
        {
            "definition_key": key,
            "name": "research_alias",
            "user_id": str(TEST_USER_ID),
            "org_id": str(TEST_ORG_ID),
            "tenant_id": str(TEST_TENANT_ID),
        }
    )
    assert tool is not None
    message = await tool.ainvoke(
        {"name": tool.name, "type": "tool_call", "id": "search-call", "args": {"query": "q"}}
    )
    assert isinstance(message, ToolMessage)
    assert message.tool_call_id == "search-call"
    assert message.status == "error"
    assert message.content == message_text
    runner.assert_awaited_once()
    assert failure.await_args is not None
    assert str(failure.await_args.args[1]) == message_text


@pytest.mark.asyncio
async def test_search_propagates_graph_control_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    key = "research_search"
    monkeypatch.setattr(registry.require(key), "runner", AsyncMock(side_effect=GraphBubbleUp))
    tool = tool_factory.create_tool_for_runtime({"definition_key": key, "name": key})
    assert tool is not None
    with pytest.raises(GraphBubbleUp):
        await tool.ainvoke({"query": "q"})
