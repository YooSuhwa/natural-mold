"""Real tool dispatch with SQLite organization policy and isolated provider transports."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import JsonValue
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import tool_factory
from app.models.organization import Organization
from app.models.research_search_usage import ResearchSearchUsage
from app.services import research_search_policy as policy
from app.tools.definitions.search_transport import ResearchSearchError, SearchUnavailable
from app.tools.domain import ToolRunContext
from app.tools.registry import registry
from tests.sqlite_test_engine import TestSession

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]
ENDPOINT = "https://internal-search.example/dept/search"


@pytest.fixture
def actor(monkeypatch: pytest.MonkeyPatch, org_fixture: OrgFixture) -> policy.ResearchSearchActor:
    monkeypatch.setattr(policy, "tenant_session", lambda _: TestSession())
    monkeypatch.setattr("app.config.settings.tavily_api_key", "only-for-search-test")
    return policy.ResearchSearchActor(org_fixture.tenant, org_fixture.first, org_fixture.user.id)


async def permit(
    db: AsyncSession, actor: policy.ResearchSearchActor, providers: list[str], budget: int = 5
) -> None:
    org = await db.get(Organization, actor.org_id)
    assert org is not None
    search_policy: dict[str, JsonValue] = {
        "providers": [*providers],
        "monthly_attempt_limit": budget,
        "searxng_endpoint": ENDPOINT if "searxng" in providers else None,
    }
    org.settings = {"research_search": search_policy}
    await db.commit()


async def test_chat_falls_back_once_preserving_period_and_source(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    actor: policy.ResearchSearchActor,
) -> None:
    await permit(db, actor, ["tavily", "searxng"])
    recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    requests: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        requests.append(req)
        if req.url.host == "api.tavily.com":
            assert req.headers["authorization"] == "Bearer only-for-search-test"
            assert json.loads(req.content) == {
                "query": "sensitive project",
                "time_range": "day",
                "max_results": 2,
                "include_published_date": True,
                "filter_by_published_date": True,
            }
            return httpx.Response(503, text="provider detail must remain private")
        assert str(req.url) == ENDPOINT
        assert "authorization" not in req.headers
        assert parse_qs(req.content.decode()) == {
            "q": ["sensitive project"],
            "format": ["json"],
            "time_range": ["day"],
        }
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "Old", "url": "https://old.example", "publishedDate": "2020-01-01"},
                    {"title": "Undated", "url": "https://no-date.example"},
                    {"title": "No source", "url": "javascript:alert(1)", "publishedDate": recent},
                    {
                        "title": "Original",
                        "url": "https://source.example/article",
                        "content": "Passage",
                        "publishedDate": recent,
                    },
                    {
                        "title": "Duplicate",
                        "url": "https://source.example/article",
                        "publishedDate": recent,
                    },
                ]
            },
        )

    caplog.set_level(logging.INFO)
    with monkeypatch.context() as m:
        for hook in ("run_pre", "run_post", "run_failure"):
            m.setattr(tool_factory.hooks, hook, AsyncMock())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            m.setattr(tool_factory, "get_tool_http_client", lambda: client)
            tool = tool_factory.create_tool_for_runtime(
                {
                    "definition_key": "research_search",
                    "name": "research_search",
                    "tenant_id": str(actor.tenant_id),
                    "org_id": str(actor.org_id),
                    "user_id": str(actor.user_id),
                }
            )
            assert tool is not None
            result = await tool.ainvoke(
                {"query": "  sensitive\n project  ", "time_range": "day", "max_results": 2}
            )
    assert len(requests) == 2
    assert result == {
        "query": "sensitive project",
        "provider": "searxng",
        "time_range": "day",
        "results": [
            {
                "title": "Original",
                "url": "https://source.example/article",
                "content": "Passage",
                "published_date": recent,
                "provider": "searxng",
            }
        ],
    }
    assert await db.scalar(select(func.sum(ResearchSearchUsage.attempts))) == 2
    assert "provider=searxng" in caplog.text
    assert "sensitive project" not in caplog.text and "only-for-search-test" not in caplog.text


@pytest.mark.parametrize(
    ("providers", "period", "limit", "status", "hosts", "error"),
    [
        ([], "", 5, 503, [], "not enabled"),
        (["searxng"], "week", 5, 503, [], "requested period"),
        (["tavily", "searxng"], "week", 5, 503, ["api.tavily.com"], "Retry later"),
        (["tavily", "searxng"], "day", 0, 200, [], "budget"),
        (["tavily", "searxng"], "day", 1, 503, ["api.tavily.com"], "budget"),
        (["tavily", "searxng"], "", 5, 403, ["api.tavily.com"], "denied access"),
        (["tavily", "searxng"], "", 5, 429, ["api.tavily.com"], "rate limit"),
        (["tavily", "searxng"], "", 5, 307, ["api.tavily.com"], "refused"),
        (
            ["tavily", "searxng"],
            "",
            5,
            503,
            ["api.tavily.com", "internal-search.example"],
            "Retry later",
        ),
    ],
)
async def test_policy_and_provider_errors_bound_external_calls(
    db: AsyncSession,
    actor: policy.ResearchSearchActor,
    providers: list[str],
    period: str,
    limit: int,
    status: int,
    hosts: list[str],
    error: str,
) -> None:
    await permit(db, actor, providers, limit)
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.host)
        return httpx.Response(
            status,
            headers={"Location": "https://unauthorized.example/search"},
            text="provider private query detail",
        )

    definition = registry.require("research_search")
    assert definition.runner is not None
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        with pytest.raises(ValueError, match=error) as raised:
            await definition.runner(
                ToolRunContext({"query": "test", "time_range": period}, None, client, actor)
            )
    assert calls == hosts
    assert "internal-search.example" not in str(raised.value)
    assert "private" not in str(raised.value)


async def test_transport_outage_and_revocation_are_rechecked_between_attempts(
    db: AsyncSession,
    actor: policy.ResearchSearchActor,
) -> None:
    await permit(db, actor, ["tavily", "searxng"])
    calls = []

    async def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.host)
        await permit(db, actor, ["tavily"])
        raise httpx.ConnectError("private provider details", request=req)

    definition = registry.require("research_search")
    assert definition.runner
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(policy.SearchPolicyError, match="not permitted"):
            await definition.runner(ToolRunContext({"query": "demo"}, None, client, actor))
    assert calls == ["api.tavily.com"]


@pytest.mark.parametrize(
    ("status", "upstream", "expected"),
    [
        (200, {"results": [], "unresponsive_engines": [["engine", "timeout"]]}, SearchUnavailable),
        (
            200,
            {"results": [], "unresponsive_engines": [["engine", "CAPTCHA"]]},
            ResearchSearchError,
        ),
        (200, {"results": None}, ResearchSearchError),
    ],
)
async def test_ambiguous_provider_failure_is_never_a_success(
    db: AsyncSession,
    actor: policy.ResearchSearchActor,
    status: int,
    upstream: dict[str, object],
    expected: type[Exception],
) -> None:
    await permit(db, actor, ["searxng"])
    definition = registry.require("research_search")
    assert definition.runner
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, json=upstream))
    ) as client:
        with pytest.raises(expected) as err:
            await definition.runner(ToolRunContext({"query": "query"}, None, client, actor))
        assert type(err.value) is expected


async def test_primary_success_preserves_source_and_does_not_fan_out(
    db: AsyncSession,
    actor: policy.ResearchSearchActor,
) -> None:
    await permit(db, actor, ["tavily", "searxng"])
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Source",
                        "url": "https://original.example/path",
                        "content": "snippet",
                        "published_date": "Fri, 06 Nov 2026 10:00:00 GMT",
                    }
                ]
            },
        )

    definition = registry.require("research_search")
    assert definition.runner
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await definition.runner(ToolRunContext({"query": "query"}, None, client, actor))
    assert seen == ["api.tavily.com"]
    assert result["results"] == [
        {
            "title": "Source",
            "url": "https://original.example/path",
            "content": "snippet",
            "published_date": "2026-11-06T10:00:00+00:00",
            "provider": "tavily",
        }
    ]


async def test_legacy_direct_api_obeys_explicit_policy_and_redacts_upstream_errors(
    db: AsyncSession,
    actor: policy.ResearchSearchActor,
) -> None:
    from app.authz.org_context import OrgContext
    from app.models.tool import Tool
    from app.tools.execution_scope import ToolRunOptions, ToolRunTarget
    from app.tools.runner import run_tool

    context = OrgContext(user_id=actor.user_id, tenant_id=actor.tenant_id, org_id=actor.org_id)
    tool = Tool(
        user_id=actor.user_id,
        tenant_id=actor.tenant_id,
        org_id=actor.org_id,
        name="Old",
        definition_key="tavily_search",
        parameters={"query": "original"},
    )
    db.add(tool)
    await db.commit()
    await db.refresh(tool)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return httpx.Response(401, text="internal response must stay private")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        options = ToolRunOptions(registry=registry, http_client=client)
        await permit(db, actor, ["searxng"])
        blocked = await run_tool(db, ToolRunTarget(tool.id, context), options)
        assert not blocked.success and not seen
        await permit(db, actor, ["tavily", "searxng"])
        failure = await run_tool(db, ToolRunTarget(tool.id, context), options)
    assert seen == ["api.tavily.com"]
    assert not failure.success and failure.http_status == 401
    assert failure.error and "denied access" in failure.error
    assert "internal response" not in failure.error


async def test_oversize_response_does_not_buffer_entire_body_or_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.tools.definitions import search_transport

    monkeypatch.setattr(search_transport, "_MAX_RESPONSE_BYTES", 65536)
    chunks = []

    async def oversized():
        for i in range(10):
            chunks.append(i)
            yield b"x" * 65536

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=oversized())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ResearchSearchError, match="maximum size"):
            await search_transport.provider_post(client, ENDPOINT)
    assert chunks == [0, 1]


async def test_shared_transport_never_sends_or_accepts_provider_cookies_across_orgs(
    db: AsyncSession,
    actor: policy.ResearchSearchActor,
    org_fixture: OrgFixture,
) -> None:
    from app.outbound.client import outbound_http_client

    second_org = await db.get(Organization, org_fixture.second)
    assert second_org is not None
    other = policy.ResearchSearchActor(second_org.tenant_id, second_org.id, actor.user_id)
    for current in (actor, other):
        await permit(db, current, ["searxng"])

    cookies = []

    def handler(req: httpx.Request) -> httpx.Response:
        cookies.append(req.headers.get("cookie"))
        assert req.headers.get("accept-encoding") == "identity"
        return httpx.Response(
            200, headers={"Set-Cookie": "provider_session=private; Path=/"}, json={"results": []}
        )

    definition = registry.require("research_search")
    assert definition.runner
    async with outbound_http_client(
        transport=httpx.MockTransport(handler), ssrf_guard=True
    ) as client:
        client.cookies.set("unrelated", "sensitive", domain="internal-search.example", path="/")
        for current in (actor, other):
            await definition.runner(ToolRunContext({"query": "safe"}, None, client, current))
        assert cookies == [None, None]
        assert client.cookies.get("provider_session") is None
        assert client.cookies.get("unrelated") == "sensitive"


async def test_malformed_legacy_row_is_a_public_terminal_error(
    actor: policy.ResearchSearchActor,
) -> None:
    definition = registry.require("tavily_search")
    assert definition.runner
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"results": [None]}))
    ) as client:
        with pytest.raises(ResearchSearchError, match="invalid result"):
            await definition.runner(ToolRunContext({"query": "safe"}, None, client, actor))
