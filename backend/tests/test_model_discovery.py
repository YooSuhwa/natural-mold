"""Tests for ``app.services.model_discovery``.

External HTTP is mocked via ``httpx.MockTransport`` so the suite is
deterministic and offline.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.models.credential import Credential
from app.models.model import Model
from app.models.user import User
from app.services import model_discovery
from app.services.model_discovery import DiscoveredModel
from tests.conftest import TEST_USER_ID

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _ensure_test_user(db: AsyncSession):
    existing = await db.execute(select(User).where(User.id == TEST_USER_ID))
    if existing.scalar_one_or_none() is None:
        db.add(User(id=TEST_USER_ID, email="dt@test", name="dt"))
        await db.commit()


async def _make_credential(
    db: AsyncSession, *, definition_key: str, data: dict
) -> Credential:
    cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key=definition_key,
        name=f"{definition_key}-test",
        data=data,
    )
    await db.commit()
    return cred


def _patch_async_client(handler: Callable[[httpx.Request], httpx.Response]):
    """Return a context manager that replaces ``httpx.AsyncClient`` with a
    mock-transport-backed instance for the lifetime of the test.
    """

    transport = httpx.MockTransport(handler)
    real_init = httpx.AsyncClient.__init__

    def _init(self, *args, **kwargs):
        kwargs.setdefault("transport", transport)
        kwargs["transport"] = transport
        real_init(self, *args, **kwargs)

    return patch.object(httpx.AsyncClient, "__init__", _init)


# ---------------------------------------------------------------------------
# OpenAI discovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_openai_filters_and_enriches(db: AsyncSession) -> None:
    """OpenAI discovery filters non-chat models and adds catalog pricing."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "gpt-4o"},
                    {"id": "gpt-4o-mini"},
                    {"id": "text-embedding-3-large"},  # filtered
                    {"id": "tts-1"},  # filtered
                ]
            },
        )

    cred = await _make_credential(db, definition_key="openai", data={"api_key": "sk-x"})

    with _patch_async_client(handler):
        results = await model_discovery.discover_from_credential(db, cred)

    names = {m.model_name for m in results}
    assert names == {"gpt-4o", "gpt-4o-mini"}
    assert all(m.provider == "openai" for m in results)


@pytest.mark.asyncio
async def test_discover_openai_marks_already_registered(
    db: AsyncSession,
) -> None:
    """Models already in the catalog table get ``already_registered=True``."""

    db.add(
        Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
    )
    await db.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]},
        )

    cred = await _make_credential(db, definition_key="openai", data={"api_key": "sk-x"})

    with _patch_async_client(handler):
        results = await model_discovery.discover_from_credential(db, cred)

    by_name = {m.model_name: m for m in results}
    assert by_name["gpt-4o"].already_registered is True
    assert by_name["gpt-4o-mini"].already_registered is False


# ---------------------------------------------------------------------------
# Anthropic discovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_anthropic_uses_static_catalog(db: AsyncSession) -> None:
    """Anthropic has no /models endpoint — discovery is offline."""

    cred = await _make_credential(
        db, definition_key="anthropic", data={"api_key": "k"}
    )

    # No HTTP mock — discovery must not make any outbound call.
    results = await model_discovery.discover_from_credential(db, cred)
    assert results, "expected at least one Anthropic model"
    assert all(m.provider == "anthropic" for m in results)
    # Pricing should come from LiteLLM enrichment.
    assert any(m.cost_per_input_token is not None for m in results)
    assert all(m.source in {"litellm", "manual"} for m in results)


# ---------------------------------------------------------------------------
# OpenRouter discovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_openrouter_uses_inline_pricing(
    db: AsyncSession,
) -> None:
    """OpenRouter pricing wins; ``source='openrouter'`` for those rows."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "anthropic/claude-haiku-4-5",
                        "name": "Claude Haiku 4.5",
                        "context_length": 200000,
                        "architecture": {
                            "input_modalities": ["text", "image"],
                            "output_modalities": ["text"],
                        },
                        "pricing": {"prompt": "0.000001", "completion": "0.000005"},
                        "top_provider": {"max_completion_tokens": 64000},
                        "supported_parameters": ["tools", "reasoning"],
                    },
                    {
                        "id": "free-only",
                        "name": "Free Only",
                        "pricing": {},
                        "architecture": {},
                        "top_provider": {},
                    },
                ]
            },
        )

    cred = await _make_credential(
        db, definition_key="openrouter", data={"api_key": "sk-or"}
    )

    with _patch_async_client(handler):
        results = await model_discovery.discover_from_credential(db, cred)

    by_name = {m.model_name: m for m in results}
    haiku = by_name["anthropic/claude-haiku-4-5"]
    assert haiku.source == "openrouter"
    assert haiku.cost_per_input_token == Decimal("0.000001")
    assert haiku.cost_per_output_token == Decimal("0.000005")
    assert haiku.context_window == 200000
    assert haiku.max_output_tokens == 64000
    assert haiku.supports_function_calling is True
    assert haiku.supports_reasoning is True
    assert haiku.supports_vision is True

    # No pricing → catalog fallback (or 'manual' if catalog also silent).
    free = by_name["free-only"]
    assert free.source in {"litellm", "manual"}


# ---------------------------------------------------------------------------
# Google GenAI discovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_google_filters_to_generate_content(
    db: AsyncSession,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-2.0-flash",
                        "displayName": "Gemini 2.0 Flash",
                        "supportedGenerationMethods": ["generateContent"],
                        "inputTokenLimit": 1048576,
                        "outputTokenLimit": 8192,
                    },
                    {
                        "name": "models/embedding-001",
                        "supportedGenerationMethods": ["embedContent"],  # filtered
                    },
                ]
            },
        )

    cred = await _make_credential(
        db, definition_key="google_genai", data={"api_key": "g-key"}
    )

    with _patch_async_client(handler):
        results = await model_discovery.discover_from_credential(db, cred)

    names = {m.model_name for m in results}
    assert names == {"gemini-2.0-flash"}
    assert results[0].provider == "google_genai"
    # context_window comes from the live ai-model-list snapshot (curated
    # registry, 1st-priority source). Older LiteLLM-only fixtures reported
    # the raw 1_048_576 token figure; the curated value rounds to 1M.
    assert results[0].context_window in {1_048_576, 1_000_000}
    assert results[0].max_output_tokens == 8192


# ---------------------------------------------------------------------------
# OpenAI Compatible discovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_openai_compatible_passes_all(db: AsyncSession) -> None:
    """Compatible endpoints surface every model the server returns."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "llama3:70b"},
                    {"id": "mistral-7b"},
                    {"id": "babbage-002"},  # would be filtered for official OpenAI
                ]
            },
        )

    cred = await _make_credential(
        db,
        definition_key="openai_compatible",
        data={"base_url": "http://localhost:11434/v1", "api_key": ""},
    )

    with _patch_async_client(handler):
        results = await model_discovery.discover_from_credential(db, cred)

    names = {m.model_name for m in results}
    assert names == {"llama3:70b", "mistral-7b", "babbage-002"}
    assert all(m.provider == "openai_compatible" for m in results)


@pytest.mark.asyncio
async def test_discover_openai_compatible_requires_base_url(
    db: AsyncSession,
) -> None:
    cred = await _make_credential(
        db,
        definition_key="openai_compatible",
        data={"api_key": "sk", "base_url": ""},
    )
    with pytest.raises(ValueError, match="base_url"):
        await model_discovery.discover_from_credential(db, cred)


# ---------------------------------------------------------------------------
# Dispatch errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_definition_raises(db: AsyncSession) -> None:
    cred = await _make_credential(
        db, definition_key="naver_search", data={"client_id": "id", "client_secret": "s"}
    )
    with pytest.raises(ValueError, match="discoverable"):
        await model_discovery.discover_from_credential(db, cred)


# ---------------------------------------------------------------------------
# DiscoveredModel.to_dict
# ---------------------------------------------------------------------------


def test_discovered_model_to_dict_serializes_decimal() -> None:
    """Decimal pricing serializes to string for JSON safety."""

    m = DiscoveredModel(
        model_name="x",
        display_name="x",
        provider="openai",
        source="litellm",
        cost_per_input_token=Decimal("0.0000003"),
    )
    payload = m.to_dict()
    assert payload["cost_per_input_token"] == "3E-7"
    assert payload["cost_per_output_token"] is None
