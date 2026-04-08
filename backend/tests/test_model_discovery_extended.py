"""Extended tests for model_discovery — test_connection edge cases, openai_compatible."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.model_discovery import discover_models
from app.services.model_discovery import test_connection as _test_connection

# ---------------------------------------------------------------------------
# test_connection — 403 error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connection_403_error():
    """test_connection handles 403 HTTPStatusError."""
    prov = MagicMock()
    prov.provider_type = "openai"
    prov.base_url = None
    prov.api_key_encrypted = "bad-key"

    mock_response = MagicMock()
    mock_response.status_code = 403

    with (
        patch("app.services.model_discovery.decrypt_api_key", return_value="bad-key"),
        patch(
            "app.services.model_discovery.discover_models",
            side_effect=httpx.HTTPStatusError("", request=MagicMock(), response=mock_response),
        ),
    ):
        success, message, count = await _test_connection(prov)

    assert success is False
    assert "접근 거부" in message
    assert count is None


# ---------------------------------------------------------------------------
# test_connection — generic HTTP error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connection_generic_http_error():
    """test_connection handles generic HTTPStatusError (e.g., 500)."""
    prov = MagicMock()
    prov.provider_type = "openai"
    prov.base_url = None
    prov.api_key_encrypted = "key"

    mock_response = MagicMock()
    mock_response.status_code = 500

    with (
        patch("app.services.model_discovery.decrypt_api_key", return_value="key"),
        patch(
            "app.services.model_discovery.discover_models",
            side_effect=httpx.HTTPStatusError("", request=MagicMock(), response=mock_response),
        ),
    ):
        success, message, count = await _test_connection(prov)

    assert success is False
    assert "HTTP 500" in message
    assert count is None


# ---------------------------------------------------------------------------
# test_connection — unexpected exception
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connection_unexpected_exception():
    """test_connection handles unexpected exceptions."""
    prov = MagicMock()
    prov.provider_type = "openai"
    prov.base_url = None
    prov.api_key_encrypted = None

    with (
        patch("app.services.model_discovery.decrypt_api_key", return_value=None),
        patch(
            "app.services.model_discovery.discover_models",
            side_effect=ValueError("Unexpected"),
        ),
    ):
        success, message, count = await _test_connection(prov)

    assert success is False
    assert "연결 테스트에 실패했습니다" in message
    assert count is None


# ---------------------------------------------------------------------------
# _discover_openai_compatible — with base_url
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_openai_compatible_with_base_url():
    """openai_compatible with base_url returns models from /models endpoint."""
    mock_provider = MagicMock()
    mock_provider.provider_type = "openai_compatible"
    mock_provider.base_url = "http://localhost:11434/v1"
    mock_provider.api_key_encrypted = None

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "data": [
            {"id": "llama3"},
            {"id": "mistral"},
        ]
    }
    mock_resp.raise_for_status = MagicMock()

    with (
        patch("app.services.model_discovery.decrypt_api_key", return_value=None),
        patch("app.services.model_discovery.httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await discover_models(mock_provider)

    names = [m.model_name for m in result]
    assert "llama3" in names
    assert "mistral" in names


# ---------------------------------------------------------------------------
# _discover_openai_compatible — Ollama fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_openai_compatible_ollama_fallback():
    """openai_compatible falls back to Ollama /api/tags on HTTPStatusError."""
    mock_provider = MagicMock()
    mock_provider.provider_type = "openai_compatible"
    mock_provider.base_url = "http://localhost:11434/v1"
    mock_provider.api_key_encrypted = None

    # First call (/models) fails, second call (/api/tags) succeeds
    mock_fail_resp = MagicMock()
    mock_fail_resp.status_code = 404
    mock_fail_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "", request=MagicMock(), response=mock_fail_resp
    )

    mock_ok_resp = MagicMock()
    mock_ok_resp.json.return_value = {
        "models": [
            {"name": "llama3:latest"},
            {"name": "codellama:latest"},
        ]
    }
    mock_ok_resp.raise_for_status = MagicMock()

    with (
        patch("app.services.model_discovery.decrypt_api_key", return_value=None),
        patch("app.services.model_discovery.httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=[mock_fail_resp, mock_ok_resp])
        mock_client_cls.return_value = mock_client

        result = await discover_models(mock_provider)

    names = [m.model_name for m in result]
    assert "codellama:latest" in names
    assert "llama3:latest" in names


# ---------------------------------------------------------------------------
# _discover_anthropic with api_key — key verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_anthropic_with_valid_key():
    """Anthropic discovery with valid key returns static model list."""
    mock_provider = MagicMock()
    mock_provider.provider_type = "anthropic"
    mock_provider.base_url = None
    mock_provider.api_key_encrypted = "enc-key"

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with (
        patch("app.services.model_discovery.decrypt_api_key", return_value="sk-ant-test"),
        patch("app.services.model_discovery.httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await discover_models(mock_provider)

    assert len(result) > 0
    names = [m.model_name for m in result]
    assert "claude-sonnet-4-20250514" in names
