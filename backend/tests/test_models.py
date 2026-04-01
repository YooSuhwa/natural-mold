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
