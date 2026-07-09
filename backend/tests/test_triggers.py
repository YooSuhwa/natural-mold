from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.models.agent_trigger_run import AgentTriggerRun
from app.models.model import Model
from tests.conftest import TEST_USER_ID, TestSession


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
            "identity_mode": "fixed",
        },
    )
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_create_trigger_rejects_per_user_agent(client: AsyncClient):
    model_id = await _create_model(client)
    resp = await client.post(
        "/api/agents",
        json={
            "name": "Per User Agent",
            "system_prompt": "You are helpful.",
            "model_id": model_id,
            "identity_mode": "per_user",
        },
    )
    agent_id = resp.json()["id"]

    trigger_resp = await client.post(
        f"/api/agents/{agent_id}/triggers",
        json={
            "trigger_type": "interval",
            "schedule_config": {"interval_minutes": 10},
            "input_message": "run",
        },
    )

    assert trigger_resp.status_code == 422
    assert "fixed" in trigger_resp.text


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
async def test_trigger_guardrails_are_persisted(client: AsyncClient):
    model_id = await _create_model(client)
    agent_id = await _create_agent(client, model_id)

    resp = await client.post(
        f"/api/agents/{agent_id}/triggers",
        json={
            "name": "제한 있는 스케줄",
            "trigger_type": "interval",
            "schedule_config": {"interval_minutes": 10},
            "input_message": "상태 확인",
            "max_runs": 3,
            "auto_pause_after_failures": 2,
            "end_at": "2026-06-30T00:00:00Z",
        },
    )

    assert resp.status_code == 201
    trigger = resp.json()
    assert trigger["max_runs"] == 3
    assert trigger["auto_pause_after_failures"] == 2
    assert trigger["end_at"] == "2026-06-30T00:00:00"
    assert trigger["failure_count"] == 0


@pytest.mark.asyncio
async def test_trigger_selected_conversation_policy_is_persisted(client: AsyncClient):
    model_id = await _create_model(client)
    agent_id = await _create_agent(client, model_id)
    conversation_resp = await client.post(
        f"/api/agents/{agent_id}/conversations",
        json={"title": "기존 세션"},
    )
    assert conversation_resp.status_code == 201
    conversation_id = conversation_resp.json()["id"]

    resp = await client.post(
        f"/api/agents/{agent_id}/triggers",
        json={
            "name": "기존 세션에 쓰기",
            "trigger_type": "interval",
            "schedule_config": {"interval_minutes": 10},
            "input_message": "상태 확인",
            "conversation_policy": "selected_conversation",
            "target_conversation_id": conversation_id,
        },
    )

    assert resp.status_code == 201
    trigger = resp.json()
    assert trigger["conversation_policy"] == "selected_conversation"
    assert trigger["target_conversation_id"] == conversation_id


@pytest.mark.asyncio
async def test_global_trigger_management_routes(client: AsyncClient):
    model_id = await _create_model(client)
    agent_id = await _create_agent(client, model_id)

    resp = await client.post(
        f"/api/agents/{agent_id}/triggers",
        json={
            "name": "아침 뉴스",
            "trigger_type": "interval",
            "schedule_config": {"interval_minutes": 15},
            "input_message": "뉴스 요약",
        },
    )
    assert resp.status_code == 201
    trigger = resp.json()
    trigger_id = trigger["id"]

    resp = await client.get("/api/triggers")
    assert resp.status_code == 200
    triggers = resp.json()
    assert len(triggers) == 1
    assert triggers[0]["name"] == "아침 뉴스"
    assert triggers[0]["agent_name"] == "Test Agent"

    resp = await client.get("/api/triggers/summary")
    assert resp.status_code == 200
    assert resp.json() == {"total_unread": 0, "active_count": 1}

    resp = await client.patch(f"/api/triggers/{trigger_id}", json={"status": "paused"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "paused"

    resp = await client.delete(f"/api/triggers/{trigger_id}")
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_global_trigger_runs_history(client: AsyncClient):
    model_id = await _create_model(client)
    agent_id = await _create_agent(client, model_id)

    resp = await client.post(
        f"/api/agents/{agent_id}/triggers",
        json={
            "trigger_type": "interval",
            "schedule_config": {"interval_minutes": 30},
            "input_message": "상태 확인",
        },
    )
    assert resp.status_code == 201
    trigger_id = resp.json()["id"]

    async with TestSession() as db:
        db.add(
            AgentTriggerRun(
                trigger_id=uuid.UUID(trigger_id),
                agent_id=uuid.UUID(agent_id),
                user_id=TEST_USER_ID,
                status="success",
                input_message="상태 확인",
            )
        )
        await db.commit()

    resp = await client.get(f"/api/triggers/{trigger_id}/runs")
    assert resp.status_code == 200
    runs = resp.json()
    assert len(runs) == 1
    assert runs[0]["status"] == "success"
    assert runs[0]["source"] == "scheduled"
    assert "duration_ms" in runs[0]
    assert "output_preview" in runs[0]
    assert "thread_id" in runs[0]


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


@pytest.mark.asyncio
async def test_create_trigger_rejects_hidden_runtime_agent(client: AsyncClient, db):
    """R2 회귀: 히든 런타임 에이전트(skill builder)는 트리거 대상이 될 수 없다 —
    트리거 실행은 빌더 분기·System LLM 재해석을 우회해 placeholder 프롬프트를
    표준 에이전트로 스케줄 실행하게 된다. 세션 응답에 agent_id가 노출되므로
    UUID를 아는 것만으로 결선이 가능하면 히든 불변식이 깨진다."""

    from app.services.skill_builder_hidden_agent import get_or_create_skill_builder_agent
    from tests.conftest import TEST_USER_ID
    from tests.skill_builder_test_helpers import configure_system_llm

    await configure_system_llm(db)
    agent = await get_or_create_skill_builder_agent(db, user_id=TEST_USER_ID)
    await db.commit()

    trigger_resp = await client.post(
        f"/api/agents/{agent.id}/triggers",
        json={
            "trigger_type": "interval",
            "schedule_config": {"interval_minutes": 10},
            "input_message": "run",
        },
    )

    assert trigger_resp.status_code == 404
    assert "AGENT_NOT_FOUND" in trigger_resp.text
