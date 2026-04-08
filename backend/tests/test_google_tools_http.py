"""Tests for google_tools — HTTP-level tests (mocked) + google_auth refresh."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Google Search — success (mocked HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_google_search_success(monkeypatch: pytest.MonkeyPatch):
    """Google web search returns parsed results on success."""
    from app.agent_runtime.google_tools import build_google_search_tool

    mock_json = {
        "searchInformation": {"totalResults": "42"},
        "items": [
            {
                "title": "Test Page",
                "link": "https://example.com",
                "snippet": "A test result.",
            }
        ],
    }

    async def mock_get(self, url, **kwargs):
        return httpx.Response(
            200,
            json=mock_json,
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    tool = build_google_search_tool(
        "web", "google_search", "Search", auth_config={"google_api_key": "k", "google_cse_id": "c"}
    )
    result = await tool.ainvoke({"query": "test"})
    assert "Test Page" in result
    assert "https://example.com" in result
    assert "42" in result


# ---------------------------------------------------------------------------
# Google Search — 403 error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_google_search_403(monkeypatch: pytest.MonkeyPatch):
    """Google search returns quota error on 403."""
    from app.agent_runtime.google_tools import build_google_search_tool

    async def mock_get(self, url, **kwargs):
        resp = httpx.Response(
            403,
            json={"error": {"message": "quota exceeded"}},
            request=httpx.Request("GET", url),
        )
        resp.raise_for_status()

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    tool = build_google_search_tool(
        "web", "google_search", "Search", auth_config={"google_api_key": "k", "google_cse_id": "c"}
    )
    result = await tool.ainvoke({"query": "test"})
    assert "Error" in result
    assert "할당량" in result


# ---------------------------------------------------------------------------
# Google Search — generic HTTP error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_google_search_500(monkeypatch: pytest.MonkeyPatch):
    """Google search returns error message on non-403 HTTP error."""
    from app.agent_runtime.google_tools import build_google_search_tool

    async def mock_get(self, url, **kwargs):
        resp = httpx.Response(
            500,
            text="Internal Server Error",
            request=httpx.Request("GET", url),
        )
        resp.raise_for_status()

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    tool = build_google_search_tool(
        "web", "google_search", "Search", auth_config={"google_api_key": "k", "google_cse_id": "c"}
    )
    result = await tool.ainvoke({"query": "test"})
    assert "Error" in result
    assert "500" in result


# ---------------------------------------------------------------------------
# Google Search — connection error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_google_search_connection_error(monkeypatch: pytest.MonkeyPatch):
    """Google search returns error on connection failure."""
    from app.agent_runtime.google_tools import build_google_search_tool

    async def mock_get(self, url, **kwargs):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    tool = build_google_search_tool(
        "web", "google_search", "Search", auth_config={"google_api_key": "k", "google_cse_id": "c"}
    )
    result = await tool.ainvoke({"query": "test"})
    assert "Error" in result
    assert "연결할 수 없습니다" in result


# ---------------------------------------------------------------------------
# Google News Search — variant parameter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_google_news_search(monkeypatch: pytest.MonkeyPatch):
    """Google news search passes tbm=nws parameter."""
    from app.agent_runtime.google_tools import build_google_search_tool

    captured_params = {}

    async def mock_get(self, url, **kwargs):
        captured_params.update(kwargs.get("params", {}))
        return httpx.Response(
            200,
            json={"items": [{"title": "News", "link": "https://news.example.com"}]},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    tool = build_google_search_tool(
        "news", "google_news", "News", auth_config={"google_api_key": "k", "google_cse_id": "c"}
    )
    await tool.ainvoke({"query": "headlines"})
    assert captured_params.get("tbm") == "nws"


# ---------------------------------------------------------------------------
# Google Image Search — variant parameter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_google_image_search(monkeypatch: pytest.MonkeyPatch):
    """Google image search passes searchType=image parameter."""
    from app.agent_runtime.google_tools import build_google_search_tool

    captured_params = {}

    async def mock_get(self, url, **kwargs):
        captured_params.update(kwargs.get("params", {}))
        return httpx.Response(
            200,
            json={"items": [{"title": "Img", "link": "https://example.com/img.jpg"}]},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    tool = build_google_search_tool(
        "image", "google_images", "Images",
        auth_config={"google_api_key": "k", "google_cse_id": "c"},
    )
    await tool.ainvoke({"query": "photos"})
    assert captured_params.get("searchType") == "image"


# ---------------------------------------------------------------------------
# Google Auth — token refresh
# ---------------------------------------------------------------------------


def test_google_auth_refresh(monkeypatch: pytest.MonkeyPatch):
    """get_google_credentials refreshes token when not valid."""
    from app.agent_runtime.google_auth import get_google_credentials
    from app.config import settings

    monkeypatch.setattr(settings, "google_oauth_client_id", "")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "")
    monkeypatch.setattr(settings, "google_oauth_refresh_token", "")

    mock_creds = MagicMock()
    mock_creds.valid = False

    with (
        patch("app.agent_runtime.google_auth.Credentials", return_value=mock_creds),
        patch("app.agent_runtime.google_auth.Request"),
    ):
        result = get_google_credentials(
            auth_config={
                "google_oauth_client_id": "test-id",
                "google_oauth_client_secret": "test-secret",
                "google_oauth_refresh_token": "test-token",
            }
        )

    assert result is mock_creds
    mock_creds.refresh.assert_called_once()


def test_google_auth_already_valid(monkeypatch: pytest.MonkeyPatch):
    """get_google_credentials skips refresh when token is valid."""
    from app.agent_runtime.google_auth import get_google_credentials
    from app.config import settings

    monkeypatch.setattr(settings, "google_oauth_client_id", "")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "")
    monkeypatch.setattr(settings, "google_oauth_refresh_token", "")

    mock_creds = MagicMock()
    mock_creds.valid = True

    with (
        patch("app.agent_runtime.google_auth.Credentials", return_value=mock_creds),
        patch("app.agent_runtime.google_auth.Request"),
    ):
        result = get_google_credentials(
            auth_config={
                "google_oauth_client_id": "test-id",
                "google_oauth_client_secret": "test-secret",
                "google_oauth_refresh_token": "test-token",
            }
        )

    assert result is mock_creds
    mock_creds.refresh.assert_not_called()
