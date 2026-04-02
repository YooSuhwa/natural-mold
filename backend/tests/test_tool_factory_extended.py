"""Extended tests for app.agent_runtime.tool_factory — HTTP tools and edge cases."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.agent_runtime.tool_factory import (
    _build_http_tool_func,
    create_builtin_tool,
    create_prebuilt_tool,
    create_tool_from_db,
)

# ---------------------------------------------------------------------------
# create_builtin_tool — individual tools
# ---------------------------------------------------------------------------


def test_builtin_web_search_returns_tool():
    tool = create_builtin_tool("Web Search")
    assert tool.name == "web_search"
    assert "검색" in tool.description


def test_builtin_web_scraper_returns_tool():
    tool = create_builtin_tool("Web Scraper")
    assert tool.name == "web_scraper"
    assert "웹" in tool.description


def test_builtin_current_datetime_returns_tool():
    tool = create_builtin_tool("Current DateTime")
    assert tool.name == "current_datetime"


def test_builtin_unknown_raises_value_error():
    with pytest.raises(ValueError, match="Unknown builtin tool"):
        create_builtin_tool("Nonexistent Tool")


# ---------------------------------------------------------------------------
# create_prebuilt_tool — mocked builders
# ---------------------------------------------------------------------------


@patch("app.agent_runtime.tool_factory.build_naver_search_tool")
def test_prebuilt_naver_blog(mock_build: MagicMock):
    mock_build.return_value = MagicMock(name="naver_blog_search")
    tool = create_prebuilt_tool("Naver Blog Search")
    mock_build.assert_called_once()
    args = mock_build.call_args
    assert args[0][0] == "blog"  # search_type
    assert tool is mock_build.return_value


@patch("app.agent_runtime.tool_factory.build_google_search_tool")
def test_prebuilt_google_search(mock_build: MagicMock):
    mock_build.return_value = MagicMock(name="google_search")
    tool = create_prebuilt_tool("Google Search")
    mock_build.assert_called_once()
    args = mock_build.call_args
    assert args[0][0] == "web"
    assert tool is mock_build.return_value


@patch("app.agent_runtime.tool_factory.build_google_chat_webhook_tool")
def test_prebuilt_google_chat_send(mock_build: MagicMock):
    mock_build.return_value = MagicMock(name="google_chat_send")
    tool = create_prebuilt_tool("Google Chat Send")
    mock_build.assert_called_once()
    assert tool is mock_build.return_value


@patch("app.agent_runtime.tool_factory.build_gmail_read_tool")
def test_prebuilt_gmail_read(mock_build: MagicMock):
    mock_build.return_value = MagicMock(name="gmail_read")
    tool = create_prebuilt_tool("Gmail Read")
    mock_build.assert_called_once()
    assert tool is mock_build.return_value


@patch("app.agent_runtime.tool_factory.build_calendar_list_events_tool")
def test_prebuilt_calendar_list(mock_build: MagicMock):
    mock_build.return_value = MagicMock(name="calendar_list_events")
    tool = create_prebuilt_tool("Calendar List Events")
    mock_build.assert_called_once()
    assert tool is mock_build.return_value


@patch("app.agent_runtime.tool_factory.build_naver_search_tool")
def test_prebuilt_with_auth_config(mock_build: MagicMock):
    mock_build.return_value = MagicMock()
    auth = {"naver_client_id": "id", "naver_client_secret": "sec"}
    create_prebuilt_tool("Naver News Search", auth_config=auth)
    call_kwargs = mock_build.call_args
    assert call_kwargs[0][3] == auth  # positional arg: auth_config


def test_prebuilt_unknown_raises():
    with pytest.raises(ValueError, match="Unknown prebuilt tool"):
        create_prebuilt_tool("Does Not Exist")


# ---------------------------------------------------------------------------
# create_tool_from_db — HTTP tool creation
# ---------------------------------------------------------------------------


def test_create_tool_from_db_get():
    tool = create_tool_from_db(
        name="weather_api",
        description="Get weather",
        api_url="https://api.weather.com/v1",
        http_method="GET",
        parameters_schema=None,
        auth_type=None,
        auth_config=None,
    )
    assert tool.name == "weather_api"
    assert tool.description == "Get weather"


def test_create_tool_from_db_post():
    tool = create_tool_from_db(
        name="send_data",
        description="Send data via POST",
        api_url="https://api.example.com/data",
        http_method="POST",
        parameters_schema=None,
        auth_type=None,
        auth_config=None,
    )
    assert tool.name == "send_data"


def test_create_tool_from_db_no_description():
    tool = create_tool_from_db(
        name="my_tool",
        description=None,
        api_url="https://example.com",
        http_method="GET",
        parameters_schema=None,
        auth_type=None,
        auth_config=None,
    )
    assert "my_tool" in tool.description


@pytest.mark.asyncio
async def test_http_tool_get_sends_params(monkeypatch: pytest.MonkeyPatch):
    """GET tool passes kwargs as query params."""
    captured: dict = {}

    async def mock_get(self, url, *, params=None, headers=None, **kw):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        return httpx.Response(200, text='{"ok":true}', request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    func = _build_http_tool_func(
        api_url="https://api.example.com/v1",
        http_method="GET",
        auth_type=None,
        auth_config=None,
    )
    result = await func(city="Seoul")
    assert captured["url"] == "https://api.example.com/v1"
    assert captured["params"]["city"] == "Seoul"
    assert '{"ok":true}' in result


@pytest.mark.asyncio
async def test_http_tool_post_sends_json(monkeypatch: pytest.MonkeyPatch):
    """POST tool passes kwargs as JSON body."""
    captured: dict = {}

    async def mock_post(self, url, *, json=None, headers=None, **kw):
        captured["json"] = json
        return httpx.Response(200, text="ok", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    func = _build_http_tool_func(
        api_url="https://api.example.com/data",
        http_method="POST",
        auth_type=None,
        auth_config=None,
    )
    result = await func(name="test")
    assert captured["json"]["name"] == "test"
    assert result == "ok"


@pytest.mark.asyncio
async def test_http_tool_api_key_auth(monkeypatch: pytest.MonkeyPatch):
    """api_key auth type sets the configured header."""
    captured: dict = {}

    async def mock_get(self, url, *, params=None, headers=None, **kw):
        captured["headers"] = headers
        return httpx.Response(200, text="ok", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    func = _build_http_tool_func(
        api_url="https://api.example.com",
        http_method="GET",
        auth_type="api_key",
        auth_config={"header_name": "X-Api-Key", "api_key": "secret123"},
    )
    await func()
    assert captured["headers"]["X-Api-Key"] == "secret123"


@pytest.mark.asyncio
async def test_http_tool_bearer_auth(monkeypatch: pytest.MonkeyPatch):
    """bearer auth type sets Authorization: Bearer header."""
    captured: dict = {}

    async def mock_get(self, url, *, params=None, headers=None, **kw):
        captured["headers"] = headers
        return httpx.Response(200, text="ok", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    func = _build_http_tool_func(
        api_url="https://api.example.com",
        http_method="GET",
        auth_type="bearer",
        auth_config={"token": "my-token-123"},
    )
    await func()
    assert captured["headers"]["Authorization"] == "Bearer my-token-123"
