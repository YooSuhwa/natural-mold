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


async def _create_agent(client: AsyncClient, model_id: str) -> str:
    resp = await client.post(
        "/api/agents",
        json={
            "name": "Opener Agent",
            "description": "test",
            "system_prompt": "prompt",
            "model_id": model_id,
        },
    )
    assert resp.status_code == 201
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_opener_questions_create_and_update(client: AsyncClient):
    """Create + PUT update with opener_questions; round-trip via GET."""
    model_id = await _create_model(client)

    # Create with opener_questions
    resp = await client.post(
        "/api/agents",
        json={
            "name": "Opener Agent",
            "description": "test",
            "system_prompt": "prompt",
            "model_id": model_id,
            "opener_questions": ["오늘 날씨?", "오늘 며칠?"],
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["opener_questions"] == ["오늘 날씨?", "오늘 며칠?"]
    agent_id = body["id"]

    # Update with new list
    resp = await client.put(
        f"/api/agents/{agent_id}",
        json={"opener_questions": ["새 질문 1", "새 질문 2", "새 질문 3"]},
    )
    assert resp.status_code == 200
    assert resp.json()["opener_questions"] == ["새 질문 1", "새 질문 2", "새 질문 3"]

    # GET round-trip
    resp = await client.get(f"/api/agents/{agent_id}")
    assert resp.status_code == 200
    assert resp.json()["opener_questions"] == ["새 질문 1", "새 질문 2", "새 질문 3"]

    # Update with empty list — clears the openers
    resp = await client.put(
        f"/api/agents/{agent_id}",
        json={"opener_questions": []},
    )
    assert resp.status_code == 200
    assert resp.json()["opener_questions"] == []


@pytest.mark.asyncio
async def test_opener_questions_validation_too_many(client: AsyncClient):
    """13 items → 422."""
    model_id = await _create_model(client)
    agent_id = await _create_agent(client, model_id)

    resp = await client.put(
        f"/api/agents/{agent_id}",
        json={"opener_questions": [f"질문 {i}" for i in range(13)]},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_opener_questions_validation_empty_item(client: AsyncClient):
    """Whitespace-only entry → 422."""
    model_id = await _create_model(client)
    agent_id = await _create_agent(client, model_id)

    resp = await client.put(
        f"/api/agents/{agent_id}",
        json={"opener_questions": ["좋은 질문", "   "]},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_opener_questions_validation_too_long(client: AsyncClient):
    """201-char item → 422."""
    model_id = await _create_model(client)
    agent_id = await _create_agent(client, model_id)

    resp = await client.put(
        f"/api/agents/{agent_id}",
        json={"opener_questions": ["a" * 201]},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_middlewares(client: AsyncClient):
    resp = await client.get("/api/middlewares")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) > 0
    types = {item["type"] for item in data}
    # deepagents 자동 주입 타입(summarization, todo_list 등)은 제외됨
    assert "summarization" not in types
    assert "tool_retry" in types
    # ``human_in_the_loop`` 는 executor 가 명시 인스턴스화하지만 사용자가
    # 도구별 ``interrupt_on`` 정책을 정의해야 동작하므로 카탈로그에 노출.
    assert "human_in_the_loop" in types


# ---------------------------------------------------------------------------
# sub-agents (M17)
# ---------------------------------------------------------------------------


async def _create_named_agent(client: AsyncClient, model_id: str, name: str) -> str:
    resp = await client.post(
        "/api/agents",
        json={
            "name": name,
            "description": f"{name} desc",
            "system_prompt": "prompt",
            "model_id": model_id,
        },
    )
    assert resp.status_code == 201
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_create_with_sub_agents(client: AsyncClient):
    """Create parent agent with sub_agent_ids → response.sub_agents populated."""
    model_id = await _create_model(client)
    sub_id = await _create_named_agent(client, model_id, "Sub Agent")

    resp = await client.post(
        "/api/agents",
        json={
            "name": "Parent Agent",
            "description": "owns sub agents",
            "system_prompt": "prompt",
            "model_id": model_id,
            "sub_agent_ids": [sub_id],
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert len(body["sub_agents"]) == 1
    assert body["sub_agents"][0]["id"] == sub_id
    assert body["sub_agents"][0]["name"] == "Sub Agent"


@pytest.mark.asyncio
async def test_update_sub_agents_replace(client: AsyncClient):
    """PUT replaces existing sub_agent_links wholesale."""
    model_id = await _create_model(client)
    sub_a = await _create_named_agent(client, model_id, "Sub A")
    sub_b = await _create_named_agent(client, model_id, "Sub B")
    sub_c = await _create_named_agent(client, model_id, "Sub C")

    # Create with [A]
    resp = await client.post(
        "/api/agents",
        json={
            "name": "Parent",
            "description": "test",
            "system_prompt": "prompt",
            "model_id": model_id,
            "sub_agent_ids": [sub_a],
        },
    )
    parent_id = resp.json()["id"]
    assert [s["id"] for s in resp.json()["sub_agents"]] == [sub_a]

    # Update to [B, C]
    resp = await client.put(
        f"/api/agents/{parent_id}",
        json={"sub_agent_ids": [sub_b, sub_c]},
    )
    assert resp.status_code == 200
    sub_ids = [s["id"] for s in resp.json()["sub_agents"]]
    assert sub_ids == [sub_b, sub_c]

    # Update with [] clears
    resp = await client.put(
        f"/api/agents/{parent_id}",
        json={"sub_agent_ids": []},
    )
    assert resp.status_code == 200
    assert resp.json()["sub_agents"] == []


@pytest.mark.asyncio
async def test_sub_agent_self_reference_reject(client: AsyncClient):
    """PUT /agents/{A} with sub_agent_ids=[A.id] → 400."""
    model_id = await _create_model(client)
    agent_id = await _create_named_agent(client, model_id, "Self")

    resp = await client.put(
        f"/api/agents/{agent_id}",
        json={"sub_agent_ids": [agent_id]},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_sub_agent_duplicate_ids_reject(client: AsyncClient):
    """sub_agent_ids 중복은 schema validator에서 422."""
    model_id = await _create_model(client)
    sub_id = await _create_named_agent(client, model_id, "Sub")

    resp = await client.post(
        "/api/agents",
        json={
            "name": "Parent",
            "description": "test",
            "system_prompt": "prompt",
            "model_id": model_id,
            "sub_agent_ids": [sub_id, sub_id],
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_sub_agent_cascade_delete(client: AsyncClient):
    """Parent 삭제 시 agent_subagents 행도 사라진다."""
    from sqlalchemy import select

    from app.models.agent_subagent import AgentSubAgentLink
    from tests.conftest import TestSession

    model_id = await _create_model(client)
    sub_id = await _create_named_agent(client, model_id, "Sub")
    resp = await client.post(
        "/api/agents",
        json={
            "name": "Parent",
            "description": "test",
            "system_prompt": "prompt",
            "model_id": model_id,
            "sub_agent_ids": [sub_id],
        },
    )
    parent_id = resp.json()["id"]

    # Confirm link exists
    async with TestSession() as session:
        result = await session.execute(select(AgentSubAgentLink))
        links = result.scalars().all()
        assert len(links) == 1

    # Delete the parent
    resp = await client.delete(f"/api/agents/{parent_id}")
    assert resp.status_code == 204

    # Confirm cascade purged the link
    async with TestSession() as session:
        result = await session.execute(select(AgentSubAgentLink))
        links = result.scalars().all()
        assert len(links) == 0


@pytest.mark.asyncio
async def test_sub_agent_mix_self_and_valid_reject(client: AsyncClient):
    """sub_agent_ids에 자기 자신 + 유효한 sub_id가 섞여 있어도 400으로 reject."""
    model_id = await _create_model(client)
    valid_sub_id = await _create_named_agent(client, model_id, "ValidSub")
    parent_id = await _create_named_agent(client, model_id, "Parent")

    resp = await client.put(
        f"/api/agents/{parent_id}",
        json={"sub_agent_ids": [valid_sub_id, parent_id]},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_sub_agent_nonexistent_id_rejected(client: AsyncClient):
    """존재하지 않는 sub_agent_id → 400 (DB FK 위반으로 500 안 됨)."""
    import uuid as _uuid

    model_id = await _create_model(client)
    parent_id = await _create_named_agent(client, model_id, "Parent")

    fake = str(_uuid.uuid4())
    resp = await client.put(
        f"/api/agents/{parent_id}",
        json={"sub_agent_ids": [fake]},
    )
    assert resp.status_code == 400
    assert "Invalid or unauthorized" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_tool_ids_nonexistent_rejected(client: AsyncClient):
    """존재하지 않는 tool_id → 400 (FK 위반 500 방지)."""
    import uuid as _uuid

    model_id = await _create_model(client)
    parent_id = await _create_named_agent(client, model_id, "Parent")

    fake = str(_uuid.uuid4())
    resp = await client.put(f"/api/agents/{parent_id}", json={"tool_ids": [fake]})
    assert resp.status_code == 400
    assert "tool_ids" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_skill_ids_nonexistent_rejected(client: AsyncClient):
    """존재하지 않는 skill_id → 400."""
    import uuid as _uuid

    model_id = await _create_model(client)
    parent_id = await _create_named_agent(client, model_id, "Parent")

    fake = str(_uuid.uuid4())
    resp = await client.put(f"/api/agents/{parent_id}", json={"skill_ids": [fake]})
    assert resp.status_code == 400
    assert "skill_ids" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_sub_agent_cross_user_owner_rejected(client: AsyncClient):
    """타 사용자가 소유한 agent를 sub_agent로 묶으려 하면 400.

    PoC라 default user가 1명이지만, 다른 user_id로 직접 INSERT한 agent를
    request로 보내면 service의 user_id 필터에 걸려 reject되어야 한다.
    """
    import uuid as _uuid

    from app.models.agent import Agent
    from tests.conftest import TestSession

    model_id = await _create_model(client)
    parent_id = await _create_named_agent(client, model_id, "Parent")

    # 다른 user_id로 직접 agent INSERT
    foreign_user_id = _uuid.uuid4()
    foreign_agent_id = _uuid.uuid4()
    async with TestSession() as session:
        session.add(
            Agent(
                id=foreign_agent_id,
                user_id=foreign_user_id,
                name="ForeignAgent",
                description="taint",
                system_prompt="x",
                model_id=_uuid.UUID(model_id),
            )
        )
        await session.commit()

    resp = await client.put(
        f"/api/agents/{parent_id}",
        json={"sub_agent_ids": [str(foreign_agent_id)]},
    )
    assert resp.status_code == 400
    assert "Invalid or unauthorized" in resp.json()["detail"]
