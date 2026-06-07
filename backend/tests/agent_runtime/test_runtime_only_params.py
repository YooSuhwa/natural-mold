"""Verify that ``runtime_only`` tool params are exposed to the LLM.

Regression for the case where the Naver News tool was created with a fixed
``query`` string at registration time, so the agent kept calling the API with
that literal value instead of the user's search keyword. The fix exposes
``runtime_only=True`` fields on the StructuredTool ``args_schema`` so the
model can fill them per call, while still allowing operator-pinned defaults.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from app.agent_runtime import tool_factory
from app.agent_runtime.tool_factory import (
    _build_runtime_args_schema,
    create_tool_for_runtime,
)
from app.tools.registry import registry as tool_registry
from tests.tool_helpers import tool_coroutine


@pytest.fixture(autouse=True)
def _reset_tool_http_client() -> Iterator[None]:
    tool_factory.reset_tool_http_client_for_tests()
    yield
    tool_factory.reset_tool_http_client_for_tests()


def _naver_news_config(
    stored_params: dict[str, Any] | None = None,
    *,
    name: str = "naver news search",
) -> dict[str, Any]:
    return {
        "tool_id": None,
        "definition_key": "naver_search_news",
        "name": name,
        "description": "Search Korean news.",
        "parameters": stored_params or {},
        "credentials": {
            "client_id": "fake-client-id",
            "client_secret": "fake-client-secret",
        },
        "credential_id": None,
        "user_id": None,
        "agent_id": None,
    }


def test_runtime_only_query_is_exposed_on_args_schema() -> None:
    """The Naver news ``query`` field is ``runtime_only`` → schema must have it."""

    definition = tool_registry.require("naver_search_news")
    model_cls = _build_runtime_args_schema(definition, stored_params={})

    assert model_cls is not None
    assert "query" in model_cls.model_fields
    # Required-with-no-default → field is required on the schema.
    assert model_cls.model_fields["query"].is_required()


def test_stored_query_becomes_schema_default_and_is_overridable() -> None:
    """When the operator pins a stored value, the LLM still sees an override."""

    definition = tool_registry.require("naver_search_news")
    model_cls = _build_runtime_args_schema(definition, stored_params={"query": "pinned"})

    assert model_cls is not None
    model_cls = cast(type[Any], model_cls)
    instance_default = model_cls()
    assert instance_default.query == "pinned"
    instance_overridden = model_cls(query="새 키워드")
    assert instance_overridden.query == "새 키워드"


def test_create_tool_for_runtime_attaches_args_schema() -> None:
    """LangChain StructuredTool must declare the args schema so the LLM sees it."""

    tool = create_tool_for_runtime(_naver_news_config())
    assert tool is not None
    schema = tool.args_schema
    assert schema is not None
    schema = cast(type[Any], schema)
    # Pydantic v2: args_schema may be a model class — confirm via fields.
    assert "query" in schema.model_fields


def test_create_tool_for_runtime_uses_vertex_safe_name_for_korean_tool_name() -> None:
    """Provider tool schemas require ASCII-ish function names."""

    tool = create_tool_for_runtime(_naver_news_config(name="네이버 뉴스 검색"))

    assert tool is not None
    assert tool.name == "naver_search_news"


def test_create_tool_for_runtime_prefixes_digit_starting_tool_name() -> None:
    tool = create_tool_for_runtime(_naver_news_config(name="123 news search"))

    assert tool is not None
    assert tool.name == "_123_news_search"


@pytest.mark.asyncio
async def test_runtime_arg_overrides_stored_query_at_invocation() -> None:
    """When the model calls the tool with a query, the runner sees that value."""

    captured: dict[str, Any] = {}

    async def fake_runner(ctx: Any) -> dict[str, Any]:
        captured["params"] = dict(ctx.parameters)
        return {"items": []}

    definition = tool_registry.require("naver_search_news")
    with patch.object(definition, "runner", fake_runner):
        tool = create_tool_for_runtime(_naver_news_config(stored_params={"query": "pinned"}))
        assert tool is not None
        # AsyncClient creation is not mocked but our fake runner never uses it.
        with patch("app.agent_runtime.tool_factory.httpx.AsyncClient") as client_cls:
            client_cls.return_value.__aenter__ = AsyncMock(return_value=object())
            client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            await tool_coroutine(tool)(query="한컴 최신 뉴스")

    assert "verify" in client_cls.call_args.kwargs
    assert captured["params"]["query"] == "한컴 최신 뉴스"


@pytest.mark.asyncio
async def test_registry_tool_reuses_shared_http_client_across_invocations() -> None:
    captured_clients: list[Any] = []

    async def fake_runner(ctx: Any) -> dict[str, Any]:
        captured_clients.append(ctx.http_client)
        return {"items": []}

    definition = tool_registry.require("naver_search_news")
    with patch.object(definition, "runner", fake_runner):
        tool = create_tool_for_runtime(_naver_news_config(stored_params={"query": "pinned"}))
        assert tool is not None
        with patch("app.agent_runtime.tool_factory.httpx.AsyncClient") as client_cls:
            client_cls.return_value.__aenter__ = AsyncMock(return_value=object())
            client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            await tool_coroutine(tool)(query="첫 번째")
            await tool_coroutine(tool)(query="두 번째")

    assert client_cls.call_count == 1
    assert len(captured_clients) == 2
    assert captured_clients[0] is captured_clients[1]
