from __future__ import annotations

import uuid

import pytest

from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.anyio


async def _seed_deployed_agent_with_key(
    db,
    *,
    scopes: list[str] | None = None,
    allow_all_deployments: bool = False,
):
    from app.agent_api import service
    from app.agent_runtime.identity import make_agent_runtime_name
    from app.models.agent import Agent
    from app.models.model import Model
    from app.models.user import User
    from app.schemas.agent_api import AgentApiKeyCreate, AgentDeploymentCreate

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
        name="Research Agent",
        description="Researches topics",
        system_prompt="You are a research agent.",
        runtime_name=make_agent_runtime_name(agent_id),
        model_id=model.id,
        model=model,
    )
    db.add_all([user, model, agent])
    await db.commit()

    deployment = await service.create_deployment(
        db, TEST_USER_ID, AgentDeploymentCreate(agent_id=agent.id)
    )
    key, cleartext = await service.create_api_key(
        db,
        TEST_USER_ID,
        AgentApiKeyCreate(
            name="Runtime key",
            scopes=scopes or ["invoke", "stream", "read"],
            allow_all_deployments=allow_all_deployments,
            deployment_ids=[] if allow_all_deployments else [deployment.id],
        ),
    )
    return agent, deployment, key, cleartext


async def _seed_two_deployments_with_scoped_keys(db):
    from app.agent_api import service
    from app.agent_runtime.identity import make_agent_runtime_name
    from app.models.agent import Agent
    from app.models.model import Model
    from app.models.user import User
    from app.schemas.agent_api import AgentApiKeyCreate, AgentDeploymentCreate

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
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    first = Agent(
        id=first_id,
        user_id=TEST_USER_ID,
        name="First Agent",
        system_prompt="You are first.",
        runtime_name=make_agent_runtime_name(first_id),
        model_id=model.id,
        model=model,
    )
    second = Agent(
        id=second_id,
        user_id=TEST_USER_ID,
        name="Second Agent",
        system_prompt="You are second.",
        runtime_name=make_agent_runtime_name(second_id),
        model_id=model.id,
        model=model,
    )
    db.add_all([user, model, first, second])
    await db.commit()

    first_deployment = await service.create_deployment(
        db, TEST_USER_ID, AgentDeploymentCreate(agent_id=first.id)
    )
    second_deployment = await service.create_deployment(
        db, TEST_USER_ID, AgentDeploymentCreate(agent_id=second.id)
    )
    _first_key, first_cleartext = await service.create_api_key(
        db,
        TEST_USER_ID,
        AgentApiKeyCreate(
            name="First key",
            scopes=["invoke", "stream", "read"],
            deployment_ids=[first_deployment.id],
        ),
    )
    _second_key, second_cleartext = await service.create_api_key(
        db,
        TEST_USER_ID,
        AgentApiKeyCreate(
            name="Second key",
            scopes=["invoke", "stream", "read"],
            deployment_ids=[second_deployment.id],
        ),
    )
    return first_deployment, second_deployment, first_cleartext, second_cleartext


async def test_public_health_is_available_without_api_key(client):
    response = await client.get("/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_public_agents_requires_api_key(client):
    response = await client.get("/v1/agents")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AGENT_API_KEY_REQUIRED"


async def test_public_agents_lists_key_allowed_deployments(client, db):
    agent, deployment, _key, cleartext = await _seed_deployed_agent_with_key(db)

    response = await client.get(
        "/v1/agents",
        headers={"Authorization": f"Bearer {cleartext}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "data": [
            {
                "id": deployment.public_id,
                "agent_id": str(agent.id),
                "name": "Research Agent",
                "description": "Researches topics",
                "status": "active",
                "capabilities": ["invoke", "stream"],
            }
        ]
    }


async def test_public_agents_lists_all_active_deployments_for_allow_all_key(client, db):
    agent, deployment, _key, cleartext = await _seed_deployed_agent_with_key(
        db, allow_all_deployments=True
    )

    response = await client.get(
        "/v1/agents",
        headers={"Authorization": f"Bearer {cleartext}"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == [
        {
            "id": deployment.public_id,
            "agent_id": str(agent.id),
            "name": "Research Agent",
            "description": "Researches topics",
            "status": "active",
            "capabilities": ["invoke", "stream"],
        }
    ]


async def test_public_agents_requires_read_scope(client, db):
    _agent, _deployment, _key, cleartext = await _seed_deployed_agent_with_key(
        db, scopes=["invoke"]
    )

    response = await client.get(
        "/v1/agents",
        headers={"Authorization": f"Bearer {cleartext}"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AGENT_API_SCOPE_REQUIRED"


async def test_thread_lookup_rejects_key_without_thread_deployment_access(client, db):
    _first_deployment, second_deployment, first_key, second_key = (
        await _seed_two_deployments_with_scoped_keys(db)
    )

    thread_response = await client.post(
        "/v1/threads",
        headers={"Authorization": f"Bearer {second_key}"},
        json={"agent_id": second_deployment.public_id, "user": "external-user-1"},
    )
    assert thread_response.status_code == 201
    thread = thread_response.json()

    response = await client.get(
        f"/v1/threads/{thread['id']}",
        headers={"Authorization": f"Bearer {first_key}"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AGENT_API_DEPLOYMENT_FORBIDDEN"


async def test_run_wait_invokes_deployed_agent_and_creates_api_conversation(
    client, db, monkeypatch
):
    from sqlalchemy import select

    from app.models.audit_event import AuditEvent
    from app.models.conversation import Conversation

    agent, deployment, _key, cleartext = await _seed_deployed_agent_with_key(db)
    seen = {}

    async def fake_invoke(cfg, messages_history, **kwargs):
        seen["cfg"] = cfg
        seen["messages"] = messages_history
        seen["kwargs"] = kwargs
        return "Hello from deployed agent"

    monkeypatch.setattr(
        "app.routers.agent_runtime_api.execute_agent_invoke",
        fake_invoke,
    )

    response = await client.post(
        "/v1/runs/wait",
        headers={"Authorization": f"Bearer {cleartext}"},
        json={
            "agent_id": deployment.public_id,
            "input": {"messages": [{"role": "user", "content": "Hi"}]},
            "user": "external-user-1",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["agent_id"] == deployment.public_id
    assert body["thread_id"] is None
    assert body["status"] == "succeeded"
    assert body["output"] == {"answer": "Hello from deployed agent"}

    assert seen["cfg"].agent_id == str(agent.id)
    assert seen["cfg"].agent_name == "Research Agent"
    assert seen["kwargs"]["moldy_source"] == "api"
    assert seen["messages"] == [{"role": "user", "content": "Hi"}]

    result = await db.execute(select(Conversation))
    conversations = list(result.scalars().all())
    assert len(conversations) == 1
    assert conversations[0].source == "api"

    audit = (
        await db.execute(
            select(AuditEvent).where(
                AuditEvent.action == "agent_api.run_wait",
                AuditEvent.target_id == body["id"],
            )
        )
    ).scalar_one()
    assert audit.actor_type == "api_key"
    assert audit.actor_api_key_id is not None
    assert audit.owner_user_id == TEST_USER_ID
    assert audit.target_type == "agent_api_run"
    assert audit.outcome == "success"
    assert "Hi" not in str(audit.event_metadata)


async def test_run_wait_marks_run_failed_when_app_error_is_raised(client, db, monkeypatch):
    from sqlalchemy import select

    from app.exceptions import ValidationError
    from app.models.agent_api import AgentApiRun

    _agent, deployment, _key, cleartext = await _seed_deployed_agent_with_key(db)

    async def fake_invoke(_cfg, _messages_history, **_kwargs):
        raise ValidationError("AGENT_API_TEST_FAILURE", "test failure")

    monkeypatch.setattr(
        "app.routers.agent_runtime_api.execute_agent_invoke",
        fake_invoke,
    )

    response = await client.post(
        "/v1/runs/wait",
        headers={"Authorization": f"Bearer {cleartext}"},
        json={
            "agent_id": deployment.public_id,
            "input": {"messages": [{"role": "user", "content": "Hi"}]},
        },
    )

    assert response.status_code == 422
    result = await db.execute(select(AgentApiRun))
    run = result.scalar_one()
    assert run.status == "failed"
    assert run.error_code == "AGENT_API_TEST_FAILURE"
    assert run.error_message == "test failure"
    assert run.finished_at is not None


async def test_thread_run_wait_reuses_thread_conversation(client, db, monkeypatch):
    from sqlalchemy import select

    from app.models.audit_event import AuditEvent
    from app.models.conversation import Conversation

    _agent, deployment, _key, cleartext = await _seed_deployed_agent_with_key(db)

    async def fake_invoke(_cfg, _messages_history, **_kwargs):
        return "Thread answer"

    monkeypatch.setattr(
        "app.routers.agent_runtime_api.execute_agent_invoke",
        fake_invoke,
    )

    thread_response = await client.post(
        "/v1/threads",
        headers={"Authorization": f"Bearer {cleartext}"},
        json={"agent_id": deployment.public_id, "user": "external-user-1"},
    )

    assert thread_response.status_code == 201
    thread = thread_response.json()
    assert thread["id"].startswith("thr_")
    assert thread["agent_id"] == deployment.public_id

    run_response = await client.post(
        f"/v1/threads/{thread['id']}/runs/wait",
        headers={"Authorization": f"Bearer {cleartext}"},
        json={
            "agent_id": deployment.public_id,
            "input": {"messages": [{"role": "user", "content": "Continue"}]},
        },
    )

    assert run_response.status_code == 200
    assert run_response.json()["thread_id"] == thread["id"]
    assert run_response.json()["output"] == {"answer": "Thread answer"}

    result = await db.execute(select(Conversation))
    conversations = list(result.scalars().all())
    assert len(conversations) == 1
    assert str(conversations[0].id) == thread["conversation_id"]

    audit_actions = (
        await db.execute(
            select(AuditEvent.action).where(
                AuditEvent.target_owner_user_id == TEST_USER_ID,
                AuditEvent.actor_type == "api_key",
            )
        )
    ).scalars().all()
    assert set(audit_actions) >= {
        "agent_api.thread_create",
        "agent_api.thread_run_wait",
    }


async def test_thread_run_wait_marks_run_failed_when_executor_fails(client, db, monkeypatch):
    from sqlalchemy import select

    from app.models.agent_api import AgentApiRun

    _agent, deployment, _key, cleartext = await _seed_deployed_agent_with_key(db)

    async def fake_invoke(_cfg, _messages_history, **_kwargs):
        raise RuntimeError("executor failed")

    monkeypatch.setattr(
        "app.routers.agent_runtime_api.execute_agent_invoke",
        fake_invoke,
    )

    thread_response = await client.post(
        "/v1/threads",
        headers={"Authorization": f"Bearer {cleartext}"},
        json={"agent_id": deployment.public_id, "user": "external-user-1"},
    )
    assert thread_response.status_code == 201
    thread = thread_response.json()

    response = await client.post(
        f"/v1/threads/{thread['id']}/runs/wait",
        headers={"Authorization": f"Bearer {cleartext}"},
        json={
            "agent_id": deployment.public_id,
            "input": {"messages": [{"role": "user", "content": "Continue"}]},
        },
    )

    assert response.status_code == 500
    result = await db.execute(select(AgentApiRun))
    run = result.scalar_one()
    assert run.status == "failed"
    assert run.error_code == "AGENT_API_RUN_FAILED"
    assert run.error_message == "executor failed"
    assert run.finished_at is not None


async def test_run_stream_emits_external_sse_events(client, db, monkeypatch):
    from sqlalchemy import select

    from app.models.audit_event import AuditEvent

    _agent, deployment, _key, cleartext = await _seed_deployed_agent_with_key(db)

    async def fake_stream(_cfg, _messages_history, **_kwargs):
        yield 'event: content_delta\ndata: {"content":"Hi"}\n\n'

    monkeypatch.setattr(
        "app.routers.agent_runtime_api.execute_agent_stream",
        fake_stream,
    )

    async with client.stream(
        "POST",
        "/v1/runs/stream",
        headers={"Authorization": f"Bearer {cleartext}"},
        json={
            "agent_id": deployment.public_id,
            "input": {"messages": [{"role": "user", "content": "Hi"}]},
        },
    ) as response:
        body = await response.aread()

    assert response.status_code == 200
    text = body.decode()
    assert "event: run_start" in text
    assert "event: message" in text
    assert '"delta":"Hi"' in text
    assert "event: run_end" in text

    audit = (
        await db.execute(
            select(AuditEvent).where(AuditEvent.action == "agent_api.run_stream")
        )
    ).scalar_one()
    assert audit.actor_type == "api_key"
    assert audit.target_type == "agent_api_run"
    assert audit.outcome == "success"
    assert "Hi" not in str(audit.event_metadata)


async def test_run_stream_marks_run_failed_when_config_build_fails(client, db, monkeypatch):
    from sqlalchemy import select

    from app.exceptions import ValidationError
    from app.models.agent_api import AgentApiRun

    _agent, deployment, _key, cleartext = await _seed_deployed_agent_with_key(db)

    async def fake_build_config(*_args, **_kwargs):
        raise ValidationError("AGENT_API_TEST_FAILURE", "test stream failure")

    monkeypatch.setattr(
        "app.routers.agent_runtime_api.runtime_service.build_config_for_run",
        fake_build_config,
    )

    response = await client.post(
        "/v1/runs/stream",
        headers={"Authorization": f"Bearer {cleartext}"},
        json={
            "agent_id": deployment.public_id,
            "input": {"messages": [{"role": "user", "content": "Hi"}]},
        },
    )

    assert response.status_code == 422
    result = await db.execute(select(AgentApiRun))
    run = result.scalar_one()
    assert run.status == "failed"
    assert run.error_code == "AGENT_API_TEST_FAILURE"
    assert run.error_message == "test stream failure"
    assert run.finished_at is not None
