from __future__ import annotations

import uuid

import pytest

from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.anyio


async def _seed_agent(db, *, name: str = "Support Agent", identity_mode: str = "fixed"):
    from app.agent_runtime.identity import make_agent_runtime_name
    from app.models.agent import Agent
    from app.models.model import Model
    from app.models.user import User

    user = User(
        id=TEST_USER_ID,
        email="owner@test.com",
        name="Owner",
        hashed_password="h",
        is_active=True,
        is_super_user=True,
    )
    model = Model(
        id=uuid.uuid4(),
        provider="openai",
        model_name="gpt-5-mini",
        display_name="GPT-5 Mini",
    )
    agent_id = uuid.uuid4()
    agent = Agent(
        id=agent_id,
        user_id=TEST_USER_ID,
        name=name,
        description="Handles support questions",
        system_prompt="You are a support agent.",
        runtime_name=make_agent_runtime_name(agent_id),
        identity_mode=identity_mode,
        model_id=model.id,
        model=model,
    )
    db.add_all([user, model, agent])
    await db.commit()
    return agent


async def test_deployment_candidates_include_owned_fixed_agents(client, db):
    agent = await _seed_agent(db)

    response = await client.get("/api/agent-api/deployment-candidates")

    assert response.status_code == 200
    body = response.json()
    assert body == [
        {
            "agent_id": str(agent.id),
            "agent_name": "Support Agent",
            "runtime_name": agent.runtime_name,
            "existing_deployment_id": None,
            "existing_public_id": None,
            "eligible": True,
            "ineligible_reason": None,
            "ineligible_reason_code": None,
            "runtime_policy": None,
        }
    ]


async def test_deployment_candidates_return_reason_code_for_per_user_agents(client, db):
    agent = await _seed_agent(db, identity_mode="per_user")

    response = await client.get("/api/agent-api/deployment-candidates")

    assert response.status_code == 200
    body = response.json()
    assert body == [
        {
            "agent_id": str(agent.id),
            "agent_name": "Support Agent",
            "runtime_name": agent.runtime_name,
            "existing_deployment_id": None,
            "existing_public_id": None,
            "eligible": False,
            "ineligible_reason": None,
            "ineligible_reason_code": "fixed_identity_required",
            "runtime_policy": None,
        }
    ]


async def test_deployment_payloads_expose_only_canonical_agent_runtime_policy(client, db):
    agent = await _seed_agent(db)
    agent.runtime_policy = {
        "version": 1,
        "todo": {"enabled": False},
    }
    await db.commit()

    candidate_response = await client.get("/api/agent-api/deployment-candidates")

    assert candidate_response.status_code == 200
    candidate = candidate_response.json()[0]
    expected_policy = {
        "version": 1,
        "filesystem": {"mode": "artifact_write"},
        "todo": {"enabled": False},
        "summarization": {"mode": "auto"},
    }
    assert candidate["runtime_policy"] == expected_policy
    assert "runtime_policy_source" not in candidate
    assert "runtime_policy_snapshot" not in candidate

    deployment_response = await client.post(
        "/api/agent-api/deployments",
        json={"agent_id": str(agent.id)},
    )

    assert deployment_response.status_code == 201
    assert deployment_response.json()["runtime_policy"] == expected_policy

    agent.runtime_policy = {
        "version": 1,
        "filesystem": {"mode": "inspect"},
        "todo": {"enabled": False},
    }
    await db.commit()
    list_response = await client.get("/api/agent-api/deployments")

    assert list_response.status_code == 200
    assert list_response.json()[0]["runtime_policy"] == {
        "version": 1,
        "filesystem": {"mode": "inspect"},
        "todo": {"enabled": False},
        "summarization": {"mode": "auto"},
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("runtime_policy", {"version": 1, "todo": {"enabled": False}}),
        ("runtime_policy_source", "server_owned"),
        ("unknown_runtime_option", True),
    ],
)
async def test_deployment_create_rejects_runtime_policy_injection(client, db, field, value):
    agent = await _seed_agent(db)
    agent.runtime_policy = {"version": 1, "todo": {"enabled": False}}
    await db.commit()

    response = await client.post(
        "/api/agent-api/deployments",
        json={"agent_id": str(agent.id), field: value},
    )

    assert response.status_code == 422
    await db.refresh(agent)
    assert agent.runtime_policy == {"version": 1, "todo": {"enabled": False}}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("runtime_policy", {"version": 1, "todo": {"enabled": False}}),
        ("runtime_policy_source", "server_owned"),
        ("unknown_runtime_option", True),
    ],
)
async def test_deployment_update_rejects_runtime_policy_injection(client, db, field, value):
    agent = await _seed_agent(db)
    agent.runtime_policy = {"version": 1, "todo": {"enabled": False}}
    await db.commit()
    deployment_response = await client.post(
        "/api/agent-api/deployments",
        json={"agent_id": str(agent.id)},
    )
    assert deployment_response.status_code == 201

    response = await client.patch(
        f"/api/agent-api/deployments/{deployment_response.json()['id']}",
        json={field: value},
    )

    assert response.status_code == 422
    await db.refresh(agent)
    assert agent.runtime_policy == {"version": 1, "todo": {"enabled": False}}


async def test_create_deployment_and_api_key_cleartext_once(client, db):
    agent = await _seed_agent(db)

    deployment_response = await client.post(
        "/api/agent-api/deployments",
        json={"agent_id": str(agent.id)},
    )

    assert deployment_response.status_code == 201
    deployment = deployment_response.json()
    assert deployment["agent_id"] == str(agent.id)
    assert deployment["agent_name"] == "Support Agent"
    assert deployment["public_id"] == agent.runtime_name
    assert deployment["status"] == "active"

    key_response = await client.post(
        "/api/agent-api/keys",
        json={
            "name": "Production key",
            "scopes": ["invoke", "stream"],
            "allow_all_deployments": False,
            "deployment_ids": [deployment["id"]],
        },
    )

    assert key_response.status_code == 201
    created = key_response.json()
    assert created["key"].startswith("moldy_sk_")
    assert created["prefix"].startswith("moldy_sk_")
    assert created["last_four"] == created["key"][-4:]
    assert created["deployments"][0]["public_id"] == agent.runtime_name

    list_response = await client.get("/api/agent-api/keys")

    assert list_response.status_code == 200
    listed = list_response.json()
    assert len(listed) == 1
    assert "key" not in listed[0]
    assert listed[0]["name"] == "Production key"
    assert listed[0]["deployments"][0]["agent_name"] == "Support Agent"


async def test_create_deployment_rejects_per_user_identity(client, db):
    agent = await _seed_agent(db, identity_mode="per_user")

    response = await client.post(
        "/api/agent-api/deployments",
        json={"agent_id": str(agent.id)},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "AGENT_API_FIXED_IDENTITY_REQUIRED"
