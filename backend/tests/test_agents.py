from __future__ import annotations

import pytest
from httpx import AsyncClient


async def _create_model(client: AsyncClient) -> str:
    resp = await client.post(
        "/api/models",
        json={
            "provider": "openai",
            "model_name": "gpt-4o",
            "display_name": "GPT-4o",
            "is_default": True,
        },
    )
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_agent_crud(client: AsyncClient):
    model_id = await _create_model(client)

    # Create
    resp = await client.post(
        "/api/agents",
        json={
            "name": "Test Agent",
            "description": "A test agent",
            "system_prompt": "You are a helpful assistant.",
            "model_id": model_id,
        },
    )
    assert resp.status_code == 201
    agent = resp.json()
    assert agent["name"] == "Test Agent"
    assert agent["model"]["display_name"] == "GPT-4o"
    agent_id = agent["id"]

    # List
    resp = await client.get("/api/agents")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # Get
    resp = await client.get(f"/api/agents/{agent_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Test Agent"

    # Update
    resp = await client.put(
        f"/api/agents/{agent_id}",
        json={"name": "Updated Agent"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated Agent"

    # Delete
    resp = await client.delete(f"/api/agents/{agent_id}")
    assert resp.status_code == 204

    resp = await client.get("/api/agents")
    assert len(resp.json()) == 0


@pytest.mark.asyncio
async def test_get_nonexistent_agent(client: AsyncClient):
    resp = await client.get("/api/agents/00000000-0000-0000-0000-000000000099")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_nonexistent_agent(client: AsyncClient):
    resp = await client.put(
        "/api/agents/00000000-0000-0000-0000-000000000099",
        json={"name": "Updated"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_toggle_favorite(client: AsyncClient):
    model_id = await _create_model(client)
    resp = await client.post(
        "/api/agents",
        json={
            "name": "Fav Agent",
            "description": "test",
            "system_prompt": "prompt",
            "model_id": model_id,
        },
    )
    agent_id = resp.json()["id"]
    assert resp.json()["is_favorite"] is False

    # Toggle on
    resp = await client.patch(f"/api/agents/{agent_id}/favorite")
    assert resp.status_code == 200
    assert resp.json()["is_favorite"] is True

    # Toggle off
    resp = await client.patch(f"/api/agents/{agent_id}/favorite")
    assert resp.status_code == 200
    assert resp.json()["is_favorite"] is False


@pytest.mark.asyncio
async def test_toggle_favorite_nonexistent(client: AsyncClient):
    resp = await client.patch("/api/agents/00000000-0000-0000-0000-000000000099/favorite")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_nonexistent_agent(client: AsyncClient):
    resp = await client.delete("/api/agents/00000000-0000-0000-0000-000000000099")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_middlewares(client: AsyncClient):
    resp = await client.get("/api/middlewares")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) > 0
    types = {item["type"] for item in data}
    # deepagents 빌트인 타입(summarization, todo_list 등)은 제외됨
    assert "summarization" not in types
    assert "tool_retry" in types
