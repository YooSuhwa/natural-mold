"""Tests for per-agent tool config (agent_tools.config merge)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


async def _setup_model_and_tool(client: AsyncClient) -> tuple[str, str]:
    """Create a model and a custom tool, return (model_id, tool_id)."""
    model_resp = await client.post(
        "/api/models",
        json={
            "provider": "openai",
            "model_name": "gpt-4o",
            "display_name": "GPT-4o",
            "is_default": True,
        },
    )
    model_id = model_resp.json()["id"]

    tool_resp = await client.post(
        "/api/tools/custom",
        json={
            "name": "Test Webhook",
            "description": "A webhook tool",
            "api_url": "https://example.com/webhook",
            "http_method": "POST",
            "auth_type": "api_key",
            "auth_config": {"header_name": "X-Key", "api_key": "global-key"},
        },
    )
    tool_id = tool_resp.json()["id"]

    return model_id, tool_id


@pytest.mark.asyncio
async def test_create_agent_with_tool_config(client: AsyncClient):
    """Agent can be created with per-tool config overrides."""
    model_id, tool_id = await _setup_model_and_tool(client)

    resp = await client.post(
        "/api/agents",
        json={
            "name": "Webhook Agent",
            "system_prompt": "You send webhooks.",
            "model_id": model_id,
            "tool_ids": [tool_id],
            "tool_configs": [
                {"tool_id": tool_id, "config": {"webhook_url": "https://chat.google/space-A"}},
            ],
        },
    )
    assert resp.status_code == 201
    agent = resp.json()
    assert len(agent["tools"]) == 1
    assert agent["tools"][0]["agent_config"] == {"webhook_url": "https://chat.google/space-A"}


@pytest.mark.asyncio
async def test_create_agent_without_tool_config(client: AsyncClient):
    """Agent can be created with tools but no per-tool config (backward compat)."""
    model_id, tool_id = await _setup_model_and_tool(client)

    resp = await client.post(
        "/api/agents",
        json={
            "name": "Basic Agent",
            "system_prompt": "Hello",
            "model_id": model_id,
            "tool_ids": [tool_id],
        },
    )
    assert resp.status_code == 201
    agent = resp.json()
    assert len(agent["tools"]) == 1
    assert agent["tools"][0]["agent_config"] is None


@pytest.mark.asyncio
async def test_update_agent_tool_config(client: AsyncClient):
    """Agent's per-tool config can be updated."""
    model_id, tool_id = await _setup_model_and_tool(client)

    # Create agent with tool
    resp = await client.post(
        "/api/agents",
        json={
            "name": "Config Agent",
            "system_prompt": "Test",
            "model_id": model_id,
            "tool_ids": [tool_id],
        },
    )
    agent_id = resp.json()["id"]

    # Update with tool config (re-send tool_ids + tool_configs)
    resp = await client.put(
        f"/api/agents/{agent_id}",
        json={
            "tool_ids": [tool_id],
            "tool_configs": [
                {"tool_id": tool_id, "config": {"webhook_url": "https://chat.google/space-B"}},
            ],
        },
    )
    assert resp.status_code == 200
    agent = resp.json()
    assert agent["tools"][0]["agent_config"] == {"webhook_url": "https://chat.google/space-B"}


@pytest.mark.asyncio
async def test_agent_response_includes_tool_config(client: AsyncClient):
    """GET /agents/{id} includes agent_config in tools."""
    model_id, tool_id = await _setup_model_and_tool(client)

    resp = await client.post(
        "/api/agents",
        json={
            "name": "Config Agent",
            "system_prompt": "Test",
            "model_id": model_id,
            "tool_ids": [tool_id],
            "tool_configs": [
                {"tool_id": tool_id, "config": {"webhook_url": "https://example.com/hook"}},
            ],
        },
    )
    agent_id = resp.json()["id"]

    # GET should also include config
    resp = await client.get(f"/api/agents/{agent_id}")
    assert resp.status_code == 200
    assert resp.json()["tools"][0]["agent_config"] == {"webhook_url": "https://example.com/hook"}

    # LIST should also include config
    resp = await client.get("/api/agents")
    assert resp.status_code == 200
    agents = resp.json()
    assert agents[0]["tools"][0]["agent_config"] == {"webhook_url": "https://example.com/hook"}
