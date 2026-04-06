"""Tests for model discovery service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.model_discovery import discover_models


@pytest.mark.asyncio
async def test_discover_openai_filters_chat_models():
    mock_provider = MagicMock()
    mock_provider.provider_type = "openai"
    mock_provider.base_url = None
    mock_provider.api_key_encrypted = "test-key"

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "data": [
            {"id": "gpt-4o"},
            {"id": "gpt-4o-mini"},
            {"id": "dall-e-3"},
            {"id": "whisper-1"},
            {"id": "text-embedding-ada-002"},
        ]
    }
    mock_resp.raise_for_status = MagicMock()

    with (
        patch("app.services.model_discovery.decrypt_api_key", return_value="test-key"),
        patch("app.services.model_discovery.httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await discover_models(mock_provider)

    # Only gpt- prefixed models should be included
    names = [m.model_name for m in result]
    assert "gpt-4o" in names
    assert "gpt-4o-mini" in names
    assert "dall-e-3" not in names
    assert "whisper-1" not in names


@pytest.mark.asyncio
async def test_discover_anthropic_returns_static_list():
    mock_provider = MagicMock()
    mock_provider.provider_type = "anthropic"
    mock_provider.base_url = None
    mock_provider.api_key_encrypted = None

    with patch("app.services.model_discovery.decrypt_api_key", return_value=None):
        result = await discover_models(mock_provider)

    names = [m.model_name for m in result]
    assert "claude-sonnet-4-20250514" in names
    assert len(result) == 3


@pytest.mark.asyncio
async def test_discover_unknown_provider_returns_empty():
    mock_provider = MagicMock()
    mock_provider.provider_type = "unknown_provider"
    mock_provider.api_key_encrypted = None

    result = await discover_models(mock_provider)
    assert result == []


@pytest.mark.asyncio
async def test_connection_success():
    """test_connection returns (True, message, count) on success."""
    from app.services.model_discovery import test_connection

    mock_provider = MagicMock()
    mock_provider.provider_type = "anthropic"
    mock_provider.base_url = None
    mock_provider.api_key_encrypted = None

    with patch("app.services.model_discovery.decrypt_api_key", return_value=None):
        success, message, count = await test_connection(mock_provider)

    assert success is True
    assert count == 3
    assert "모델 검색 성공" in message


@pytest.mark.asyncio
async def test_connection_auth_failure():
    """test_connection handles 401 HTTPStatusError."""
    import httpx

    from app.services.model_discovery import test_connection

    mock_provider = MagicMock()
    mock_provider.provider_type = "openai"
    mock_provider.base_url = None
    mock_provider.api_key_encrypted = "bad-key"

    mock_response = MagicMock()
    mock_response.status_code = 401

    with (
        patch("app.services.model_discovery.decrypt_api_key", return_value="bad-key"),
        patch(
            "app.services.model_discovery.discover_models",
            side_effect=httpx.HTTPStatusError("", request=MagicMock(), response=mock_response),
        ),
    ):
        success, message, count = await test_connection(mock_provider)

    assert success is False
    assert "인증 실패" in message
    assert count is None


@pytest.mark.asyncio
async def test_connection_connect_error():
    """test_connection handles ConnectError."""
    import httpx

    from app.services.model_discovery import test_connection

    mock_provider = MagicMock()
    mock_provider.provider_type = "openai_compatible"
    mock_provider.base_url = "http://localhost:99999"
    mock_provider.api_key_encrypted = None

    with (
        patch("app.services.model_discovery.decrypt_api_key", return_value=None),
        patch(
            "app.services.model_discovery.discover_models",
            side_effect=httpx.ConnectError("Connection refused"),
        ),
    ):
        success, message, count = await test_connection(mock_provider)

    assert success is False
    assert "연결 실패" in message
    assert count is None


@pytest.mark.asyncio
async def test_discover_google_filters_generate_content():
    """Google discovery filters models by supportedGenerationMethods."""
    mock_provider = MagicMock()
    mock_provider.provider_type = "google"
    mock_provider.base_url = None
    mock_provider.api_key_encrypted = "test-key"

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "models": [
            {
                "name": "models/gemini-2.0-flash",
                "displayName": "Gemini 2.0 Flash",
                "supportedGenerationMethods": ["generateContent"],
                "inputTokenLimit": 1048576,
            },
            {
                "name": "models/text-embedding-004",
                "displayName": "Embedding 004",
                "supportedGenerationMethods": ["embedContent"],
            },
        ]
    }
    mock_resp.raise_for_status = MagicMock()

    with (
        patch("app.services.model_discovery.decrypt_api_key", return_value="test-key"),
        patch("app.services.model_discovery.httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await discover_models(mock_provider)

    names = [m.model_name for m in result]
    assert "gemini-2.0-flash" in names
    assert "text-embedding-004" not in names


@pytest.mark.asyncio
async def test_discover_openai_compatible_no_base_url():
    """openai_compatible without base_url returns empty list."""
    mock_provider = MagicMock()
    mock_provider.provider_type = "openai_compatible"
    mock_provider.base_url = None
    mock_provider.api_key_encrypted = None

    with patch("app.services.model_discovery.decrypt_api_key", return_value=None):
        result = await discover_models(mock_provider)

    assert result == []


@pytest.mark.asyncio
async def test_discover_openrouter():
    """OpenRouter discovery returns models from /api/v1/models."""
    mock_provider = MagicMock()
    mock_provider.provider_type = "openrouter"
    mock_provider.base_url = None
    mock_provider.api_key_encrypted = "test-key"

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "data": [
            {
                "id": "openai/gpt-4o",
                "name": "GPT-4o",
                "context_length": 128000,
                "pricing": {"prompt": "0.0000025", "completion": "0.00001"},
            },
            {
                "id": "anthropic/claude-sonnet-4-20250514",
                "name": "Claude Sonnet 4",
                "context_length": 200000,
                "pricing": {"prompt": "0.000003", "completion": "0.000015"},
            },
        ]
    }
    mock_resp.raise_for_status = MagicMock()

    with (
        patch("app.services.model_discovery.decrypt_api_key", return_value="test-key"),
        patch("app.services.model_discovery.httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await discover_models(mock_provider)

    assert len(result) == 2
    names = [m.model_name for m in result]
    assert "anthropic/claude-sonnet-4-20250514" in names
    assert "openai/gpt-4o" in names
    # Verify context_window is populated
    gpt4o = next(m for m in result if m.model_name == "openai/gpt-4o")
    assert gpt4o.context_window == 128000
    assert gpt4o.display_name == "GPT-4o"
