from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.models.model import Model
from tests.conftest import TestSession


async def _create_model(client: AsyncClient) -> str:
    """Insert a default Model row directly — POST /api/models is gone in M5."""
    async with TestSession() as db:
        model = Model(
            provider="openai",
            model_name="gpt-4o",
            display_name="GPT-4o",
            is_default=True,
        )
        db.add(model)
        await db.commit()
        await db.refresh(model)
        return str(model.id)


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
async def test_update_trigger_not_found(client: AsyncClient):
    """Update trigger with wrong id returns 404."""
    model_id = await _create_model(client)
    agent_id = await _create_agent(client, model_id)

    resp = await client.put(
        f"/api/agents/{agent_id}/triggers/00000000-0000-0000-0000-000000000099",
        json={"status": "paused"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_trigger_not_found(client: AsyncClient):
    """Delete trigger with wrong id returns 404."""
    model_id = await _create_model(client)
    agent_id = await _create_agent(client, model_id)

    resp = await client.delete(
        f"/api/agents/{agent_id}/triggers/00000000-0000-0000-0000-000000000099"
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_trigger_reactivate(client: AsyncClient):
    """Update trigger status from paused to active re-registers the job."""
    model_id = await _create_model(client)
    agent_id = await _create_agent(client, model_id)

    # Create
    resp = await client.post(
        f"/api/agents/{agent_id}/triggers",
        json={
            "trigger_type": "interval",
            "schedule_config": {"interval_minutes": 10},
            "input_message": "test",
        },
    )
    trigger_id = resp.json()["id"]

    # Pause
    resp = await client.put(
        f"/api/agents/{agent_id}/triggers/{trigger_id}",
        json={"status": "paused"},
    )
    assert resp.json()["status"] == "paused"

    # Reactivate
    resp = await client.put(
        f"/api/agents/{agent_id}/triggers/{trigger_id}",
        json={"status": "active"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"


@pytest.mark.asyncio
async def test_update_trigger_schedule_change(client: AsyncClient):
    """Update trigger schedule re-registers the job."""
    model_id = await _create_model(client)
    agent_id = await _create_agent(client, model_id)

    resp = await client.post(
        f"/api/agents/{agent_id}/triggers",
        json={
            "trigger_type": "interval",
            "schedule_config": {"interval_minutes": 10},
            "input_message": "test",
        },
    )
    trigger_id = resp.json()["id"]

    # Update schedule config
    resp = await client.put(
        f"/api/agents/{agent_id}/triggers/{trigger_id}",
        json={"schedule_config": {"interval_minutes": 30}},
    )
    assert resp.status_code == 200
    assert resp.json()["schedule_config"]["interval_minutes"] == 30


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
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_TRIGGER_TYPE"

    # Interval without minutes
    resp = await client.post(
        f"/api/agents/{agent_id}/triggers",
        json={
            "trigger_type": "interval",
            "schedule_config": {},
            "input_message": "test",
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_SCHEDULE_CONFIG"


@pytest.mark.asyncio
async def test_list_tools_includes_user_and_system(client: AsyncClient):
    """list_tools returns both system-owned (user_id=NULL) and user tools."""
    from app.models.tool import Tool
    from tests.conftest import TEST_USER_ID, TestSession

    async with TestSession() as db:
        db.add(
            Tool(
                name="System Tool",
                definition_key="builtin:web_search",
                description="sys",
            )
        )
        db.add(
            Tool(
                name="User Tool",
                definition_key="http_request",
                user_id=TEST_USER_ID,
                description="usr",
            )
        )
        await db.commit()

    resp = await client.get("/api/tools")
    assert resp.status_code == 200
    names = {t["name"] for t in resp.json()}
    assert "System Tool" in names
    assert "User Tool" in names
