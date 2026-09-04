from __future__ import annotations

import uuid

import pytest

from tests.test_agent_runtime_public_api import _seed_deployed_agent_with_key

pytestmark = pytest.mark.anyio


async def test_dify_chat_messages_blocking_adapter(client, db, monkeypatch):
    agent, deployment, _key, cleartext = await _seed_deployed_agent_with_key(db)
    agent.runtime_policy = {"version": 1, "todo": {"enabled": False}}
    await db.commit()

    async def fake_invoke(cfg, messages_history, **_kwargs):
        assert messages_history[-1]["content"] == "요약해줘"
        assert cfg.runtime_policy.source == "stored"
        assert cfg.runtime_policy.effective.todo.enabled is False
        return "Dify style answer"

    monkeypatch.setattr(
        "app.routers.agent_runtime_api.execute_agent_invoke",
        fake_invoke,
    )

    response = await client.post(
        f"/v1/agents/{deployment.public_id}/chat-messages",
        headers={"Authorization": f"Bearer {cleartext}"},
        json={"query": "요약해줘", "response_mode": "blocking", "user": "abc-123"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Dify style answer"
    assert body["conversation_id"].startswith("thr_")
    assert body["message_id"].startswith("run_")


async def test_dify_existing_thread_rejects_noncanonical_runtime_policy_snapshot(
    client, db, monkeypatch
):
    from app.agent_runtime.runtime_policy import LEGACY_RUNTIME_POLICY
    from app.models.conversation import Conversation

    _agent, deployment, _key, cleartext = await _seed_deployed_agent_with_key(db)
    thread_response = await client.post(
        "/v1/threads",
        headers={"Authorization": f"Bearer {cleartext}"},
        json={"agent_id": deployment.public_id, "user": "external-user-1"},
    )
    assert thread_response.status_code == 201
    thread = thread_response.json()

    conversation = await db.get(Conversation, uuid.UUID(thread["conversation_id"]))
    assert conversation is not None
    conversation.runtime_policy_snapshot = {"version": 1}
    conversation.runtime_policy_version = 1
    conversation.runtime_policy_hash = LEGACY_RUNTIME_POLICY.policy_hash
    conversation.runtime_policy_source = "legacy_compat"
    await db.commit()

    async def fail_invoke(*_args, **_kwargs):
        raise AssertionError("invalid policy must reject before invocation")

    monkeypatch.setattr("app.routers.agent_runtime_api.execute_agent_invoke", fail_invoke)
    response = await client.post(
        f"/v1/agents/{deployment.public_id}/chat-messages",
        headers={"Authorization": f"Bearer {cleartext}"},
        json={
            "query": "Continue",
            "response_mode": "blocking",
            "conversation_id": thread["id"],
            "user": "external-user-1",
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "RUNTIME_POLICY_SNAPSHOT_INVALID",
            "message": "RUNTIME_POLICY_SNAPSHOT_INVALID",
        }
    }


async def test_dify_workflow_run_blocking_adapter(client, db, monkeypatch):
    _agent, deployment, _key, cleartext = await _seed_deployed_agent_with_key(db)

    async def fake_invoke(_cfg, messages_history, **_kwargs):
        assert messages_history[-1]["content"] == "Run workflow"
        return "Workflow answer"

    monkeypatch.setattr(
        "app.routers.agent_runtime_api.execute_agent_invoke",
        fake_invoke,
    )

    response = await client.post(
        "/v1/workflows/run",
        headers={"Authorization": f"Bearer {cleartext}"},
        json={
            "inputs": {"agent_id": deployment.public_id, "query": "Run workflow"},
            "response_mode": "blocking",
            "user": "abc-123",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["outputs"]["answer"] == "Workflow answer"


async def test_openai_chat_completions_blocking_adapter(client, db, monkeypatch):
    _agent, deployment, _key, cleartext = await _seed_deployed_agent_with_key(db)

    async def fake_invoke(_cfg, messages_history, **_kwargs):
        assert messages_history[-1]["content"] == "Hello"
        return "OpenAI compatible answer"

    monkeypatch.setattr(
        "app.routers.agent_runtime_api.execute_agent_invoke",
        fake_invoke,
    )

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {cleartext}"},
        json={
            "model": deployment.public_id,
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == deployment.public_id
    assert body["choices"][0]["message"]["content"] == "OpenAI compatible answer"


async def test_openai_chat_completions_streaming_adapter_emits_openai_chunks(
    client, db, monkeypatch
):
    _agent, deployment, _key, cleartext = await _seed_deployed_agent_with_key(db)

    async def fake_stream(_cfg, messages_history, **_kwargs):
        assert messages_history[-1]["content"] == "Hello"
        yield 'event: content_delta\ndata: {"content":"Hel"}\n\n'
        yield 'event: content_delta\ndata: {"content":"lo"}\n\n'

    monkeypatch.setattr(
        "app.routers.agent_runtime_api.execute_agent_stream",
        fake_stream,
    )

    async with client.stream(
        "POST",
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {cleartext}"},
        json={
            "model": deployment.public_id,
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
        },
    ) as response:
        body = (await response.aread()).decode()

    assert response.status_code == 200
    assert "event: run_start" not in body
    assert "chat.completion.chunk" in body
    assert '"delta":{"role":"assistant"}' in body
    assert '"delta":{"content":"Hel"}' in body
    assert '"delta":{"content":"lo"}' in body
    assert '"finish_reason":"stop"' in body
    assert "data: [DONE]" in body
