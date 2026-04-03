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


async def _create_agent(client: AsyncClient, model_id: str) -> str:
    resp = await client.post(
        "/api/agents",
        json={
            "name": "Test Agent",
            "system_prompt": "You are helpful.",
            "model_id": model_id,
        },
    )
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_trigger_crud(client: AsyncClient):
    model_id = await _create_model(client)
    agent_id = await _create_agent(client, model_id)

    # Create trigger
    resp = await client.post(
        f"/api/agents/{agent_id}/triggers",
        json={
            "trigger_type": "interval",
            "schedule_config": {"interval_minutes": 10},
            "input_message": "한글과컴퓨터 최신 뉴스 검색해줘",
        },
    )
    assert resp.status_code == 201
    trigger = resp.json()
    assert trigger["trigger_type"] == "interval"
    assert trigger["schedule_config"]["interval_minutes"] == 10
    assert trigger["status"] == "active"
    trigger_id = trigger["id"]

    # List triggers
    resp = await client.get(f"/api/agents/{agent_id}/triggers")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # Update trigger — pause
    resp = await client.put(
        f"/api/agents/{agent_id}/triggers/{trigger_id}",
        json={"status": "paused"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "paused"

    # Delete trigger
    resp = await client.delete(f"/api/agents/{agent_id}/triggers/{trigger_id}")
    assert resp.status_code == 204

    # Verify deleted
    resp = await client.get(f"/api/agents/{agent_id}/triggers")
    assert len(resp.json()) == 0


@pytest.mark.asyncio
async def test_trigger_validation(client: AsyncClient):
    model_id = await _create_model(client)
    agent_id = await _create_agent(client, model_id)

    # Invalid trigger type
    resp = await client.post(
        f"/api/agents/{agent_id}/triggers",
        json={
            "trigger_type": "webhook",
            "schedule_config": {},
            "input_message": "test",
        },
    )
    assert resp.status_code == 400

    # Interval without minutes
    resp = await client.post(
        f"/api/agents/{agent_id}/triggers",
        json={
            "trigger_type": "interval",
            "schedule_config": {},
            "input_message": "test",
        },
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_system_tool_not_deletable(client: AsyncClient):
    """System tools created via seed should not be deletable."""
    from app.models.tool import Tool
    from tests.conftest import TestSession

    # Create a system tool directly in DB
    async with TestSession() as db:
        tool = Tool(
            name="Web Search",
            type="builtin",
            is_system=True,
            description="Test system tool",
        )
        db.add(tool)
        await db.commit()
        await db.refresh(tool)
        tool_id = tool.id

    # Try to delete — should fail (404 because delete_tool returns False)
    resp = await client.delete(f"/api/tools/{tool_id}")
    assert resp.status_code == 404

    # Tool should still exist
    resp = await client.get("/api/tools")
    assert resp.status_code == 200
    tool_names = [t["name"] for t in resp.json()]
    assert "Web Search" in tool_names


@pytest.mark.asyncio
async def test_list_tools_includes_system(client: AsyncClient):
    """list_tools should return both user tools and system tools."""
    from app.models.tool import Tool
    from tests.conftest import TEST_USER_ID, TestSession

    async with TestSession() as db:
        # Create a system tool
        db.add(Tool(name="System Tool", type="builtin", is_system=True, description="sys"))
        # Create a user tool
        db.add(Tool(name="User Tool", type="custom", user_id=TEST_USER_ID, description="usr"))
        await db.commit()

    resp = await client.get("/api/tools")
    assert resp.status_code == 200
    names = {t["name"] for t in resp.json()}
    assert "System Tool" in names
    assert "User Tool" in names
