from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_models_empty(client: AsyncClient):
    resp = await client.get("/api/models")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_create_and_list_model(client: AsyncClient):
    data = {
        "provider": "openai",
        "model_name": "gpt-4o",
        "display_name": "GPT-4o",
        "is_default": True,
        "cost_per_input_token": "0.0000025",
        "cost_per_output_token": "0.00001",
    }
    resp = await client.post("/api/models", json=data)
    assert resp.status_code == 201
    model = resp.json()
    assert model["display_name"] == "GPT-4o"
    assert model["provider"] == "openai"
    model_id = model["id"]

    resp = await client.get("/api/models")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp = await client.delete(f"/api/models/{model_id}")
    assert resp.status_code == 204

    resp = await client.get("/api/models")
    assert len(resp.json()) == 0


@pytest.mark.asyncio
async def test_delete_nonexistent_model(client: AsyncClient):
    resp = await client.delete("/api/models/00000000-0000-0000-0000-000000000099")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_bulk_create_models(client: AsyncClient):
    """POST /api/models/bulk — create multiple models at once."""
    # Create a provider first
    resp = await client.post(
        "/api/providers",
        json={"name": "BulkProvider", "provider_type": "openai"},
    )
    provider_id = resp.json()["id"]

    data = {
        "provider_id": provider_id,
        "models": [
            {"model_name": "gpt-4o", "display_name": "GPT-4o", "context_window": 128000},
            {"model_name": "gpt-4o-mini", "display_name": "GPT-4o Mini", "context_window": 128000},
        ],
    }
    resp = await client.post("/api/models/bulk", json=data)
    assert resp.status_code == 201
    models = resp.json()
    assert len(models) == 2
    names = {m["model_name"] for m in models}
    assert names == {"gpt-4o", "gpt-4o-mini"}
    assert all(m["provider"] == "openai" for m in models)
    assert all(m["provider_id"] == provider_id for m in models)


@pytest.mark.asyncio
async def test_create_model_with_provider_id(client: AsyncClient):
    """Creating a model with provider_id links it to the provider."""
    resp = await client.post(
        "/api/providers",
        json={"name": "LinkedProvider", "provider_type": "anthropic"},
    )
    provider_id = resp.json()["id"]

    resp = await client.post(
        "/api/models",
        json={
            "provider": "anthropic",
            "model_name": "claude-sonnet-4-20250514",
            "display_name": "Claude Sonnet 4",
            "provider_id": provider_id,
        },
    )
    assert resp.status_code == 201
    model = resp.json()
    assert model["provider_id"] == provider_id
    assert model["provider_name"] == "LinkedProvider"


@pytest.mark.asyncio
async def test_bulk_create_skips_duplicates(client: AsyncClient):
    """Bulk create should skip models that already exist for same provider."""
    resp = await client.post(
        "/api/providers",
        json={"name": "DupProvider", "provider_type": "openai"},
    )
    provider_id = resp.json()["id"]

    data = {
        "provider_id": provider_id,
        "models": [{"model_name": "gpt-4o", "display_name": "GPT-4o"}],
    }
    resp = await client.post("/api/models/bulk", json=data)
    assert resp.status_code == 201
    assert len(resp.json()) == 1

    # Same model again — should be skipped
    resp = await client.post("/api/models/bulk", json=data)
    assert resp.status_code == 201
    assert len(resp.json()) == 0


@pytest.mark.asyncio
async def test_enrich_model_unknown_model():
    """enrich_model returns base dict with display_name for unknown models."""
    from app.services.model_metadata import enrich_model

    result = enrich_model("totally-unknown-model-xyz")
    assert result["display_name"] == "totally-unknown-model-xyz"
    assert result.get("context_window") is None


@pytest.mark.asyncio
async def test_model_response_includes_agent_count(client: AsyncClient):
    """Models API should return agent_count field."""
    resp = await client.post(
        "/api/models",
        json={"provider": "openai", "model_name": "gpt-test", "display_name": "Test"},
    )
    assert resp.status_code == 201

    resp = await client.get("/api/models")
    models = resp.json()
    assert len(models) > 0
    assert "agent_count" in models[0]
    assert models[0]["agent_count"] == 0
