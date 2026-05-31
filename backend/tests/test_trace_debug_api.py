from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.user import User
from app.services import trace_storage
from tests.conftest import TEST_USER_ID, TestSession


async def _seed_conversation(*, owner_id: uuid.UUID = TEST_USER_ID) -> uuid.UUID:
    async with TestSession() as db:
        db.add(User(id=owner_id, email=f"{owner_id}@test.com", name="Trace Debugger"))
        model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
        db.add(model)
        await db.flush()
        agent = Agent(
            user_id=owner_id,
            name="Trace Debugger Agent",
            description=None,
            system_prompt="You debug traces.",
            model_id=model.id,
            status="active",
        )
        db.add(agent)
        await db.flush()
        conv = Conversation(agent_id=agent.id, title="Trace Debugger")
        db.add(conv)
        await db.commit()
        return conv.id


def _events(msg_id: str, *, failed: bool = False) -> list[dict]:
    body = [
        {
            "id": f"{msg_id}-1",
            "event": "message_start",
            "data": {
                "id": msg_id,
                "input": {"messages": [{"role": "user", "content": "debug this trace"}]},
            },
        },
        {
            "id": f"{msg_id}-2",
            "event": "tool_call_start",
            "data": {"name": "web_search", "args": {"query": "moldy"}},
        },
    ]
    if failed:
        body.append(
            {
                "id": f"{msg_id}-3",
                "event": "error",
                "data": {"message": "provider failed"},
            }
        )
    body.append(
        {
            "id": f"{msg_id}-4",
            "event": "message_end",
            "data": {
                "content": "done",
                "usage": {"prompt_tokens": 3, "completion_tokens": 5},
                "status": "failed" if failed else "completed",
            },
        }
    )
    return body


async def _seed_trace(
    conversation_id: uuid.UUID,
    *,
    run_id: str = "run-debugger",
    trace_id: str = "lf-trace-debugger",
    failed: bool = False,
) -> None:
    async with TestSession() as db:
        await trace_storage.record_turn(
            db,
            conversation_id=conversation_id,
            events=_events(run_id, failed=failed),
            status="failed" if failed else "completed",
            external_trace_provider="langfuse",
            external_trace_id=trace_id,
            external_trace_url=f"https://langfuse.local/project/moldy/traces/{trace_id}",
        )
        await db.commit()


@pytest.mark.asyncio
async def test_debug_traces_requires_auth(raw_client: AsyncClient) -> None:
    conv_id = await _seed_conversation()
    await _seed_trace(conv_id)

    response = await raw_client.get(f"/api/conversations/{conv_id}/debug/traces")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_debug_traces_hides_other_users_conversation(client: AsyncClient) -> None:
    conv_id = await _seed_conversation(owner_id=uuid.uuid4())
    await _seed_trace(conv_id)

    response = await client.get(f"/api/conversations/{conv_id}/debug/traces")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_debug_traces_returns_correlated_turn_summaries(client: AsyncClient) -> None:
    conv_id = await _seed_conversation()
    await _seed_trace(conv_id, run_id="run-debugger", trace_id="lf-trace-debugger")

    response = await client.get(f"/api/conversations/{conv_id}/debug/traces")

    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == str(conv_id)
    assert body["traces"][0]["trace_id"] == "lf-trace-debugger"
    assert body["traces"][0]["provider"] == "langfuse"
    assert body["traces"][0]["moldy_run_id"] == "run-debugger"
    assert body["traces"][0]["status"] == "completed"
    assert body["traces"][0]["total_tokens"] == 8
    assert body["traces"][0]["langfuse_url"].endswith("/lf-trace-debugger")


@pytest.mark.asyncio
async def test_debug_trace_detail_falls_back_to_message_events(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conv_id = await _seed_conversation()
    await _seed_trace(
        conv_id,
        run_id="run-failed",
        trace_id="lf-trace-failed",
        failed=True,
    )

    async def _fake_fetch(*_args, **_kwargs):
        return [], "langfuse unavailable"

    monkeypatch.setattr(
        "app.services.trace_debug_service.fetch_langfuse_observations",
        _fake_fetch,
    )

    response = await client.get(f"/api/conversations/{conv_id}/debug/traces/lf-trace-failed")

    assert response.status_code == 200
    body = response.json()
    assert body["trace"]["trace_id"] == "lf-trace-failed"
    assert body["trace"]["status"] == "failed"
    assert body["fallback_reason"] == "langfuse unavailable"
    assert body["spans"][0]["name"] == "Moldy assistant turn"
    assert body["spans"][0]["input"] == {
        "messages": [{"role": "user", "content": "debug this trace"}]
    }
    assert any(span["kind"] == "error" for span in body["spans"])


@pytest.mark.asyncio
async def test_debug_trace_detail_roots_orphan_langfuse_observations(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conv_id = await _seed_conversation()
    await _seed_trace(conv_id, trace_id="lf-trace-orphan")

    async def _fake_fetch(*_args, **_kwargs):
        return [
            {
                "id": "obs-child",
                "parentObservationId": "missing-parent",
                "name": "ChatOpenAI",
                "type": "GENERATION",
                "level": "DEFAULT",
                "input": {"messages": [{"role": "user", "content": "debug this trace"}]},
            }
        ], None

    monkeypatch.setattr(
        "app.services.trace_debug_service.fetch_langfuse_observations",
        _fake_fetch,
    )

    response = await client.get(f"/api/conversations/{conv_id}/debug/traces/lf-trace-orphan")

    assert response.status_code == 200
    body = response.json()
    assert body["spans"][0]["id"] == "obs-child"
    assert body["spans"][0]["parent_id"] is None
    assert body["spans"][0]["input"] == {
        "messages": [{"role": "user", "content": "debug this trace"}]
    }
