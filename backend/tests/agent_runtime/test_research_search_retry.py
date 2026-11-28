"""Search policy and provider failover own all dispatches inside one agent tool call."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import httpx
import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import tool_factory
from app.agent_runtime.middleware_registry import build_middleware_instances
from app.models.organization import Organization
from app.models.research_search_usage import ResearchSearchUsage
from app.services import research_search_policy
from tests.sqlite_test_engine import TestSession

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture


pytest_plugins = ["tests.authz.org_context_fixture"]


class _Model(FakeMessagesListChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> _Model:
        return self


@pytest.mark.parametrize(
    ("status", "expected_hosts", "message"),
    [
        (429, ["api.tavily.com"], "limit"),
        (503, ["api.tavily.com", "search.example.com"], "Retry later"),
    ],
)
async def test_search_failure_returns_once_through_retrying_agent(
    monkeypatch: pytest.MonkeyPatch,
    org_fixture: OrgFixture,
    db: AsyncSession,
    status: int,
    expected_hosts: list[str],
    message: str,
) -> None:
    org = await db.get(Organization, org_fixture.first)
    assert org is not None
    org.settings = {
        "research_search": {
            "providers": ["tavily", "searxng"],
            "searxng_endpoint": "https://search.example.com/team/search",
            "monthly_attempt_limit": 20,
        }
    }
    await db.commit()
    monkeypatch.setattr(research_search_policy, "tenant_session", lambda _: TestSession())
    monkeypatch.setattr("app.config.settings.tavily_api_key", "testing")
    for hook in ("run_pre", "run_post", "run_failure"):
        monkeypatch.setattr(tool_factory.hooks, hook, AsyncMock())

    hosts: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(status, text="Private provider diagnostic")

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        monkeypatch.setattr(tool_factory, "get_tool_http_client", lambda: client)
        tool = tool_factory.create_tool_for_runtime(
            {
                "definition_key": "research_search",
                "name": "research_search",
                "org_id": str(org_fixture.first),
                "tenant_id": str(org_fixture.tenant),
                "user_id": str(org_fixture.user.id),
            }
        )
        assert tool is not None
        retry = build_middleware_instances(
            [{"type": "tool_retry", "params": {"max_retries": 2, "initial_delay": 0}}]
        )
        assert len(retry) == 1
        model = _Model(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {"id": "requested-search", "name": tool.name, "args": {"query": "example"}}
                    ],
                ),
                AIMessage(content="Search was unavailable."),
            ]
        )
        graph = create_agent(model, tools=[tool], middleware=retry)
        state = await graph.ainvoke({"messages": [HumanMessage(content="Research this")]})

    assert hosts == expected_hosts
    assert await db.scalar(select(ResearchSearchUsage.attempts)) == len(expected_hosts)
    responses = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert len(responses) == 1
    assert responses[0].tool_call_id == "requested-search"
    assert responses[0].status == "error"
    assert message in responses[0].content
    assert "Private provider diagnostic" not in responses[0].content
    assert state["messages"][-1].content == "Search was unavailable."
