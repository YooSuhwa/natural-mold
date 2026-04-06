"""Tests for Provider CRUD API endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_providers_empty(client: AsyncClient):
    resp = await client.get("/api/providers")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_create_provider(client: AsyncClient):
    data = {"name": "TestOpenAI", "provider_type": "openai"}
    resp = await client.post("/api/providers", json=data)
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "TestOpenAI"
    assert body["provider_type"] == "openai"
    assert body["has_api_key"] is False
    assert body["model_count"] == 0
    assert body["is_active"] is True


@pytest.mark.asyncio
async def test_create_provider_with_api_key(client: AsyncClient):
    data = {
        "name": "TestAnthropic",
        "provider_type": "anthropic",
        "api_key": "sk-ant-test-key",
    }
    resp = await client.post("/api/providers", json=data)
    assert resp.status_code == 201
    body = resp.json()
    assert body["has_api_key"] is True
    # api_key itself should not be exposed
    assert "api_key" not in body
    assert "api_key_encrypted" not in body


@pytest.mark.asyncio
async def test_update_provider(client: AsyncClient):
    # Create
    resp = await client.post(
        "/api/providers",
        json={"name": "Old Name", "provider_type": "openai"},
    )
    provider_id = resp.json()["id"]

    # Update
    resp = await client.put(
        f"/api/providers/{provider_id}",
        json={"name": "New Name", "api_key": "new-key"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "New Name"
    assert body["has_api_key"] is True


@pytest.mark.asyncio
async def test_delete_provider(client: AsyncClient):
    resp = await client.post(
        "/api/providers",
        json={"name": "ToDelete", "provider_type": "google"},
    )
    provider_id = resp.json()["id"]

    resp = await client.delete(f"/api/providers/{provider_id}")
    assert resp.status_code == 204

    resp = await client.get("/api/providers")
    assert len(resp.json()) == 0


@pytest.mark.asyncio
async def test_delete_nonexistent_provider(client: AsyncClient):
    resp = await client.delete("/api/providers/00000000-0000-0000-0000-000000000099")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_nonexistent_provider(client: AsyncClient):
    resp = await client.put(
        "/api/providers/00000000-0000-0000-0000-000000000099",
        json={"name": "Nothing"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_provider_with_base_url(client: AsyncClient):
    data = {
        "name": "Local Ollama",
        "provider_type": "openai_compatible",
        "base_url": "http://localhost:11434/v1",
    }
    resp = await client.post("/api/providers", json=data)
    assert resp.status_code == 201
    body = resp.json()
    assert body["base_url"] == "http://localhost:11434/v1"
    assert body["provider_type"] == "openai_compatible"


@pytest.mark.asyncio
async def test_test_provider_connection(client: AsyncClient):
    """POST /api/providers/{id}/test — success path."""
    from unittest.mock import AsyncMock, patch

    resp = await client.post(
        "/api/providers",
        json={"name": "TestProvider", "provider_type": "openai", "api_key": "sk-test"},
    )
    provider_id = resp.json()["id"]

    with patch(
        "app.routers.providers.model_discovery.test_connection",
        new_callable=AsyncMock,
        return_value=(True, "2개 모델 검색 성공", 2),
    ):
        resp = await client.post(f"/api/providers/{provider_id}/test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["models_count"] == 2


@pytest.mark.asyncio
async def test_test_provider_not_found(client: AsyncClient):
    resp = await client.post("/api/providers/00000000-0000-0000-0000-000000000099/test")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_discover_models_endpoint(client: AsyncClient):
    """GET /api/providers/{id}/discover-models — success path."""
    from unittest.mock import AsyncMock, patch

    from app.schemas.llm_provider import DiscoveredModel

    resp = await client.post(
        "/api/providers",
        json={"name": "TestProvider", "provider_type": "openai"},
    )
    provider_id = resp.json()["id"]

    mock_models = [
        DiscoveredModel(model_name="gpt-4o", display_name="GPT-4o"),
    ]
    with patch(
        "app.routers.providers.model_discovery.discover_models",
        new_callable=AsyncMock,
        return_value=mock_models,
    ):
        resp = await client.get(f"/api/providers/{provider_id}/discover-models")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["model_name"] == "gpt-4o"


@pytest.mark.asyncio
async def test_discover_models_not_found(client: AsyncClient):
    resp = await client.get("/api/providers/00000000-0000-0000-0000-000000000099/discover-models")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_providers_with_model_count(client: AsyncClient):
    """After creating a provider + model, model_count should be 1."""
    resp = await client.post(
        "/api/providers",
        json={"name": "CountTest", "provider_type": "openai"},
    )
    provider_id = resp.json()["id"]

    await client.post(
        "/api/models",
        json={
            "provider": "openai",
            "model_name": "gpt-4o",
            "display_name": "GPT-4o",
            "provider_id": provider_id,
        },
    )

    resp = await client.get("/api/providers")
    providers = resp.json()
    assert len(providers) == 1
    assert providers[0]["model_count"] == 1
