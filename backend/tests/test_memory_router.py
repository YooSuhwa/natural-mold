from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from tests.conftest import seed_agent


async def _seed_agent(db: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    _user, _model, agent = await seed_agent(
        db,
        name="Memory Agent",
        system_prompt="You remember useful preferences.",
    )
    conversation = Conversation(agent_id=agent.id, title="Memory Test")
    db.add(conversation)
    await db.commit()
    return agent.id, conversation.id


@pytest.mark.asyncio
async def test_user_memory_settings_default_and_patch(client: AsyncClient) -> None:
    response = await client.get("/api/me/memory-settings")

    assert response.status_code == 200
    assert response.json() == {
        "memory_enabled": True,
        "memory_read_enabled": True,
        "memory_write_policy": "ask",
        "allowed_scopes": "both",
        "trigger_memory_write_policy": "off",
    }

    response = await client.patch(
        "/api/me/memory-settings",
        json={
            "memory_write_policy": "auto",
            "allowed_scopes": "user",
            "trigger_memory_write_policy": "auto",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["memory_write_policy"] == "auto"
    assert body["allowed_scopes"] == "user"
    assert body["trigger_memory_write_policy"] == "auto"


@pytest.mark.asyncio
async def test_user_memory_settings_patch_rejects_explicit_null(
    client: AsyncClient,
) -> None:
    response = await client.patch(
        "/api/me/memory-settings",
        json={"memory_enabled": None},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_agent_memory_settings_default_and_patch(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    agent_id, _conversation_id = await _seed_agent(db)

    response = await client.get(f"/api/agents/{agent_id}/memory-settings")

    assert response.status_code == 200
    assert response.json() == {
        "memory_policy_override": "inherit",
        "memory_scopes_override": "inherit",
        "trigger_memory_policy_override": "inherit",
    }

    response = await client.patch(
        f"/api/agents/{agent_id}/memory-settings",
        json={
            "memory_policy_override": "auto",
            "memory_scopes_override": "agent_only",
            "trigger_memory_policy_override": "off",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["memory_policy_override"] == "auto"
    assert body["memory_scopes_override"] == "agent_only"
    assert body["trigger_memory_policy_override"] == "off"


@pytest.mark.asyncio
async def test_agent_memory_settings_patch_rejects_explicit_null(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    agent_id, _conversation_id = await _seed_agent(db)

    response = await client.patch(
        f"/api/agents/{agent_id}/memory-settings",
        json={"memory_policy_override": None},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_memory_record_crud_is_owner_scoped(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    agent_id, conversation_id = await _seed_agent(db)

    create_response = await client.post(
        "/api/memories",
        json={
            "scope": "agent",
            "agent_id": str(agent_id),
            "content": "This agent should summarize search results as a table first.",
            "reason": "The user configured the agent output style.",
            "source_conversation_id": str(conversation_id),
        },
    )

    assert create_response.status_code == 201
    memory = create_response.json()
    assert memory["scope"] == "agent"
    assert memory["agent_id"] == str(agent_id)
    assert memory["status"] == "active"

    list_response = await client.get(f"/api/memories?scope=agent&agent_id={agent_id}")
    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()] == [memory["id"]]

    patch_response = await client.patch(
        f"/api/memories/{memory['id']}",
        json={"content": "This agent should lead reports with a concise table."},
    )
    assert patch_response.status_code == 200
    assert patch_response.json()["content"] == (
        "This agent should lead reports with a concise table."
    )

    delete_response = await client.delete(f"/api/memories/{memory['id']}")
    assert delete_response.status_code == 204

    list_response = await client.get(f"/api/memories?scope=agent&agent_id={agent_id}")
    assert list_response.status_code == 200
    assert list_response.json() == []


@pytest.mark.asyncio
async def test_memory_record_patch_rejects_explicit_null_content(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    agent_id, conversation_id = await _seed_agent(db)
    create_response = await client.post(
        "/api/memories",
        json={
            "scope": "agent",
            "agent_id": str(agent_id),
            "content": "This agent should summarize search results as a table first.",
            "source_conversation_id": str(conversation_id),
        },
    )
    assert create_response.status_code == 201
    memory = create_response.json()

    patch_response = await client.patch(
        f"/api/memories/{memory['id']}",
        json={"content": None},
    )

    assert patch_response.status_code == 422


@pytest.mark.asyncio
async def test_memory_proposal_approve_edit_and_reject(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    agent_id, conversation_id = await _seed_agent(db)

    proposal_response = await client.post(
        "/api/memory-proposals",
        json={
            "scope": "user",
            "content": "The user's preferred language is Korean.",
            "reason": "The user explicitly asked to remember it.",
            "agent_id": str(agent_id),
            "conversation_id": str(conversation_id),
            "source_run_id": "run-1",
        },
    )

    assert proposal_response.status_code == 201
    proposal = proposal_response.json()
    assert proposal["status"] == "pending"

    get_proposal_response = await client.get(f"/api/memory-proposals/{proposal['id']}")
    assert get_proposal_response.status_code == 200
    assert get_proposal_response.json()["id"] == proposal["id"]
    assert get_proposal_response.json()["status"] == "pending"

    approve_response = await client.post(
        f"/api/memory-proposals/{proposal['id']}/edit-and-approve",
        json={"content": "The user prefers concise Korean answers."},
    )
    assert approve_response.status_code == 200
    approved = approve_response.json()
    assert approved["proposal"]["status"] == "approved"
    assert approved["memory"]["content"] == "The user prefers concise Korean answers."

    get_approved_response = await client.get(f"/api/memory-proposals/{proposal['id']}")
    assert get_approved_response.status_code == 200
    assert get_approved_response.json()["status"] == "approved"
    assert get_approved_response.json()["content"] == ("The user prefers concise Korean answers.")

    reject_source = await client.post(
        "/api/memory-proposals",
        json={
            "scope": "agent",
            "content": "Use a verbose style.",
            "reason": "Rejected in the UI.",
            "agent_id": str(agent_id),
            "conversation_id": str(conversation_id),
        },
    )
    assert reject_source.status_code == 201

    reject_response = await client.post(
        f"/api/memory-proposals/{reject_source.json()['id']}/reject"
    )
    assert reject_response.status_code == 200
    assert reject_response.json()["status"] == "rejected"
