from __future__ import annotations

import pytest

ALL_BUILTIN_NAMES = [
    "Web Search",
    "Web Scraper",
    "Current DateTime",
]

ALL_PREBUILT_NAMES = [
    "Naver Blog Search",
    "Naver News Search",
    "Naver Image Search",
    "Naver Shopping Search",
    "Naver Local Search",
    "Google Search",
    "Google News Search",
    "Google Image Search",
    "Google Chat Send",
    "Gmail Read",
    "Gmail Send",
    "Calendar List Events",
    "Calendar Create Event",
    "Calendar Update Event",
]


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_builtin_tool_factory():
    """Test that all builtin tools can be created."""
    from app.agent_runtime.tool_factory import create_builtin_tool

    for name in ALL_BUILTIN_NAMES:
        tool = create_builtin_tool(name)
        assert tool is not None
        assert tool.name
        assert tool.description


@pytest.mark.asyncio
async def test_prebuilt_tool_factory():
    """Test that all prebuilt tools can be created."""
    from app.agent_runtime.tool_factory import create_prebuilt_tool

    for name in ALL_PREBUILT_NAMES:
        tool = create_prebuilt_tool(name)
        assert tool is not None
        assert tool.name
        assert tool.description


@pytest.mark.asyncio
async def test_builtin_tool_unknown():
    """Unknown builtin tool raises ValueError."""
    from app.agent_runtime.tool_factory import create_builtin_tool

    with pytest.raises(ValueError, match="Unknown builtin tool"):
        create_builtin_tool("Nonexistent Tool")


@pytest.mark.asyncio
async def test_current_datetime_tool():
    """Current DateTime tool returns a formatted string."""
    from app.agent_runtime.tool_factory import create_builtin_tool

    tool = create_builtin_tool("Current DateTime")
    result = await tool.ainvoke({})
    assert "년" in result
    assert "월" in result
    assert "KST" in result


@pytest.mark.asyncio
async def test_prebuilt_tool_with_auth_config():
    """Prebuilt tools accept optional auth_config without error."""
    from app.agent_runtime.tool_factory import create_prebuilt_tool

    auth = {"naver_client_id": "test_id", "naver_client_secret": "test_secret"}
    tool = create_prebuilt_tool("Naver Blog Search", auth_config=auth)
    assert tool is not None
    assert tool.name == "naver_blog_search"


@pytest.mark.asyncio
async def test_prebuilt_tool_unknown():
    """Unknown prebuilt tool raises ValueError."""
    from app.agent_runtime.tool_factory import create_prebuilt_tool

    with pytest.raises(ValueError, match="Unknown prebuilt tool"):
        create_prebuilt_tool("Nonexistent Prebuilt Tool")


# ---------------------------------------------------------------------------
# Naver tools unit tests
# ---------------------------------------------------------------------------


def test_strip_html_tags():
    """Naver response HTML tags are stripped correctly."""
    from app.agent_runtime.naver_tools import _strip_html_tags

    assert _strip_html_tags("<b>테스트</b>") == "테스트"
    assert _strip_html_tags("일반 텍스트") == "일반 텍스트"
    assert _strip_html_tags("<b>bold</b> and <i>italic</i>") == "bold and italic"
    assert _strip_html_tags("&amp; &lt; &gt;") == "& < >"


def test_parse_naver_response_empty():
    """Empty Naver response is handled."""
    from app.agent_runtime.naver_tools import _parse_naver_response

    result = _parse_naver_response({"items": []})
    assert "검색 결과가 없습니다" in result


def test_parse_naver_response_blog():
    """Naver blog response is parsed correctly."""
    from app.agent_runtime.naver_tools import _parse_naver_response

    data = {
        "total": 1,
        "items": [
            {
                "title": "<b>테스트</b> 블로그",
                "link": "https://blog.naver.com/test",
                "description": "<b>테스트</b> 설명입니다",
                "bloggername": "테스터",
                "postdate": "20260401",
            }
        ],
    }
    result = _parse_naver_response(data)
    assert "테스트 블로그" in result  # HTML stripped
    assert "<b>" not in result
    assert "https://blog.naver.com/test" in result
    assert "테스터" in result


def test_parse_naver_response_shopping():
    """Naver shopping response is parsed correctly."""
    from app.agent_runtime.naver_tools import _parse_naver_response

    data = {
        "total": 1,
        "items": [
            {
                "title": "아이폰 16 Pro",
                "link": "https://shopping.naver.com/test",
                "lprice": "1500000",
                "hprice": "1800000",
                "mallName": "애플스토어",
                "category": "휴대폰",
            }
        ],
    }
    result = _parse_naver_response(data)
    assert "아이폰 16 Pro" in result
    assert "1500000원" in result
    assert "애플스토어" in result


def test_parse_naver_response_local():
    """Naver local response is parsed correctly."""
    from app.agent_runtime.naver_tools import _parse_naver_response

    data = {
        "total": 1,
        "items": [
            {
                "title": "맛있는 식당",
                "link": "https://map.naver.com/test",
                "category": "한식",
                "address": "서울특별시 강남구",
                "telephone": "02-1234-5678",
            }
        ],
    }
    result = _parse_naver_response(data)
    assert "맛있는 식당" in result
    assert "서울특별시 강남구" in result
    assert "02-1234-5678" in result


# ---------------------------------------------------------------------------
# Google tools unit tests
# ---------------------------------------------------------------------------


def test_parse_google_response_empty():
    """Empty Google response is handled."""
    from app.agent_runtime.google_tools import _parse_google_response

    result = _parse_google_response({"items": []})
    assert "검색 결과가 없습니다" in result

    result2 = _parse_google_response({})
    assert "검색 결과가 없습니다" in result2


def test_parse_google_response_web():
    """Google web search response is parsed correctly."""
    from app.agent_runtime.google_tools import _parse_google_response

    data = {
        "searchInformation": {"totalResults": "100"},
        "items": [
            {
                "title": "Test Result",
                "link": "https://example.com",
                "snippet": "This is a test snippet.",
            }
        ],
    }
    result = _parse_google_response(data)
    assert "Test Result" in result
    assert "https://example.com" in result
    assert "test snippet" in result


def test_parse_google_response_image():
    """Google image search response is parsed correctly."""
    from app.agent_runtime.google_tools import _parse_google_response

    data = {
        "items": [
            {
                "title": "Test Image",
                "link": "https://example.com/image.jpg",
                "image": {
                    "contextLink": "https://example.com/page",
                    "width": 1920,
                    "height": 1080,
                },
            }
        ],
    }
    result = _parse_google_response(data)
    assert "Test Image" in result
    assert "1920x1080" in result


# ---------------------------------------------------------------------------
# Naver tool integration test (mocked HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_naver_blog_search_no_key(monkeypatch: pytest.MonkeyPatch):
    """Naver tool returns error when API keys are not set."""
    from app.agent_runtime.naver_tools import build_naver_search_tool
    from app.config import settings

    monkeypatch.setattr(settings, "naver_client_id", "")
    monkeypatch.setattr(settings, "naver_client_secret", "")

    tool = build_naver_search_tool("blog", "naver_blog_search", "테스트", auth_config=None)
    result = await tool.ainvoke({"query": "테스트"})
    assert "Error" in result
    assert "NAVER_CLIENT_ID" in result


@pytest.mark.asyncio
async def test_google_search_no_key(monkeypatch: pytest.MonkeyPatch):
    """Google tool returns error when API keys are not set."""
    from app.agent_runtime.google_tools import build_google_search_tool
    from app.config import settings

    monkeypatch.setattr(settings, "google_api_key", "")
    monkeypatch.setattr(settings, "google_cse_id", "")

    tool = build_google_search_tool("web", "google_search", "테스트", auth_config=None)
    result = await tool.ainvoke({"query": "test"})
    assert "Error" in result
    assert "GOOGLE_API_KEY" in result


# ---------------------------------------------------------------------------
# Google Chat Webhook tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_google_chat_webhook_no_url(monkeypatch: pytest.MonkeyPatch):
    """Google Chat Webhook returns error when URL is not set."""
    from app.agent_runtime.google_workspace_tools import build_google_chat_webhook_tool
    from app.config import settings

    monkeypatch.setattr(settings, "google_chat_webhook_url", "")

    tool = build_google_chat_webhook_tool(auth_config=None)
    result = await tool.ainvoke({"text": "테스트 메시지"})
    assert "Error" in result
    assert "Webhook URL" in result


@pytest.mark.asyncio
async def test_google_chat_webhook_with_auth_config():
    """Google Chat Webhook tool can be created with auth_config."""
    from app.agent_runtime.google_workspace_tools import build_google_chat_webhook_tool

    tool = build_google_chat_webhook_tool(
        auth_config={"webhook_url": "https://chat.googleapis.com/v1/spaces/test"}
    )
    assert tool is not None
    assert tool.name == "google_chat_send"
    assert "Google Chat" in tool.description


@pytest.mark.asyncio
async def test_google_chat_webhook_success(monkeypatch: pytest.MonkeyPatch):
    """Google Chat Webhook sends message successfully (mocked)."""
    import httpx

    from app.agent_runtime.google_workspace_tools import build_google_chat_webhook_tool

    async def mock_post(self, url, **kwargs):
        return httpx.Response(
            200,
            json={"name": "spaces/test/messages/123"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    tool = build_google_chat_webhook_tool(
        auth_config={"webhook_url": "https://chat.googleapis.com/v1/spaces/test"}
    )
    result = await tool.ainvoke({"text": "테스트 메시지"})
    assert "전송되었습니다" in result


# ---------------------------------------------------------------------------
# Gmail tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gmail_read_no_credentials(monkeypatch: pytest.MonkeyPatch):
    """Gmail Read returns error when OAuth2 credentials are not set."""
    from app.agent_runtime.google_workspace_tools import build_gmail_read_tool
    from app.config import settings

    monkeypatch.setattr(settings, "google_oauth_client_id", "")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "")
    monkeypatch.setattr(settings, "google_oauth_refresh_token", "")

    tool = build_gmail_read_tool(auth_config=None)
    result = await tool.ainvoke({"query": "is:unread"})
    assert "Error" in result
    assert "OAuth2" in result


@pytest.mark.asyncio
async def test_gmail_send_no_credentials(monkeypatch: pytest.MonkeyPatch):
    """Gmail Send returns error when OAuth2 credentials are not set."""
    from app.agent_runtime.google_workspace_tools import build_gmail_send_tool
    from app.config import settings

    monkeypatch.setattr(settings, "google_oauth_client_id", "")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "")
    monkeypatch.setattr(settings, "google_oauth_refresh_token", "")

    tool = build_gmail_send_tool(auth_config=None)
    result = await tool.ainvoke({"to": "test@example.com", "subject": "Test", "body": "Hello"})
    assert "Error" in result
    assert "OAuth2" in result


@pytest.mark.asyncio
async def test_gmail_read_tool_creation():
    """Gmail Read tool can be created with auth_config."""
    from app.agent_runtime.google_workspace_tools import build_gmail_read_tool

    tool = build_gmail_read_tool(auth_config={"google_oauth_client_id": "test"})
    assert tool is not None
    assert tool.name == "gmail_read"
    assert "Gmail" in tool.description


@pytest.mark.asyncio
async def test_gmail_send_tool_creation():
    """Gmail Send tool can be created with auth_config."""
    from app.agent_runtime.google_workspace_tools import build_gmail_send_tool

    tool = build_gmail_send_tool(auth_config={"google_oauth_client_id": "test"})
    assert tool is not None
    assert tool.name == "gmail_send"
    assert "Gmail" in tool.description


# ---------------------------------------------------------------------------
# Calendar tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_calendar_list_events_no_credentials(monkeypatch: pytest.MonkeyPatch):
    """Calendar List Events returns error when OAuth2 credentials are not set."""
    from app.agent_runtime.google_workspace_tools import build_calendar_list_events_tool
    from app.config import settings

    monkeypatch.setattr(settings, "google_oauth_client_id", "")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "")
    monkeypatch.setattr(settings, "google_oauth_refresh_token", "")

    tool = build_calendar_list_events_tool(auth_config=None)
    result = await tool.ainvoke({"days": 1})
    assert "Error" in result
    assert "OAuth2" in result


@pytest.mark.asyncio
async def test_calendar_create_event_no_credentials(monkeypatch: pytest.MonkeyPatch):
    """Calendar Create Event returns error when OAuth2 credentials are not set."""
    from app.agent_runtime.google_workspace_tools import build_calendar_create_event_tool
    from app.config import settings

    monkeypatch.setattr(settings, "google_oauth_client_id", "")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "")
    monkeypatch.setattr(settings, "google_oauth_refresh_token", "")

    tool = build_calendar_create_event_tool(auth_config=None)
    result = await tool.ainvoke(
        {
            "summary": "테스트 미팅",
            "start_datetime": "2026-04-05T10:00:00+09:00",
            "end_datetime": "2026-04-05T11:00:00+09:00",
        }
    )
    assert "Error" in result
    assert "OAuth2" in result


@pytest.mark.asyncio
async def test_calendar_update_event_no_credentials(monkeypatch: pytest.MonkeyPatch):
    """Calendar Update Event returns error when OAuth2 credentials are not set."""
    from app.agent_runtime.google_workspace_tools import build_calendar_update_event_tool
    from app.config import settings

    monkeypatch.setattr(settings, "google_oauth_client_id", "")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "")
    monkeypatch.setattr(settings, "google_oauth_refresh_token", "")

    tool = build_calendar_update_event_tool(auth_config=None)
    result = await tool.ainvoke({"event_id": "test123"})
    assert "Error" in result
    assert "OAuth2" in result


@pytest.mark.asyncio
async def test_calendar_tool_creation():
    """All Calendar tools can be created with auth_config."""
    from app.agent_runtime.google_workspace_tools import (
        build_calendar_create_event_tool,
        build_calendar_list_events_tool,
        build_calendar_update_event_tool,
    )

    list_tool = build_calendar_list_events_tool(auth_config={"google_oauth_client_id": "test"})
    assert list_tool.name == "calendar_list_events"

    create_tool = build_calendar_create_event_tool(auth_config={"google_oauth_client_id": "test"})
    assert create_tool.name == "calendar_create_event"

    update_tool = build_calendar_update_event_tool(auth_config={"google_oauth_client_id": "test"})
    assert update_tool.name == "calendar_update_event"


def test_google_auth_no_credentials(monkeypatch: pytest.MonkeyPatch):
    """Google auth returns None when credentials are missing."""
    from app.agent_runtime.google_auth import get_google_credentials
    from app.config import settings

    monkeypatch.setattr(settings, "google_oauth_client_id", "")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "")
    monkeypatch.setattr(settings, "google_oauth_refresh_token", "")

    creds = get_google_credentials(auth_config=None)
    assert creds is None


# ---------------------------------------------------------------------------
# Seed data test
# ---------------------------------------------------------------------------


def test_default_tools_count():
    """All 17 default tools are defined in seed data."""
    from app.seed.default_tools import DEFAULT_TOOLS

    assert len(DEFAULT_TOOLS) == 17
    names = [t["name"] for t in DEFAULT_TOOLS]
    for expected in ALL_BUILTIN_NAMES + ALL_PREBUILT_NAMES:
        assert expected in names, f"Missing tool in seed data: {expected}"

    builtin_names = set(ALL_BUILTIN_NAMES)
    for t in DEFAULT_TOOLS:
        if t["name"] in builtin_names:
            assert t["type"] == "builtin"
        else:
            assert t["type"] == "prebuilt"
        assert t["is_system"] is True
        assert t["description"]
        assert t["parameters_schema"]
