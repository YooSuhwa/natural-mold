"""Tests for app.routers.shares — share link lifecycle + public visitor surface."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.agent_runtime.protocol_events import stored_protocol_event
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.message_event import MessageEvent
from app.models.model import Model
from app.models.user import User
from app.schemas.conversation import MessageResponse
from tests.conftest import TEST_USER_ID, TestSession


async def _seed_conversation(*, owner_id: uuid.UUID = TEST_USER_ID) -> uuid.UUID:
    """Seed a User + Model + Agent + Conversation owned by ``owner_id``."""
    async with TestSession() as db:
        # The mock current user is auto-seeded by conftest; only seed a
        # different owner when the test asks for one.
        if owner_id != TEST_USER_ID:
            other = User(id=owner_id, email=f"{owner_id}@test.com", name="Other")
            db.add(other)
            await db.flush()
        else:
            existing = await db.get(User, TEST_USER_ID)
            if existing is None:
                db.add(User(id=TEST_USER_ID, email="test@test.com", name="Test"))
                await db.flush()

        model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
        db.add(model)
        await db.flush()

        agent = Agent(
            user_id=owner_id,
            name="Share Agent",
            description="An agent for share tests",
            system_prompt="You are helpful.",
            model_id=model.id,
        )
        db.add(agent)
        await db.flush()

        conv = Conversation(agent_id=agent.id, title="Shared topic")
        db.add(conv)
        await db.commit()
        return conv.id


def _shared_assistant_message(conversation_id: uuid.UUID, message_id: uuid.UUID) -> MessageResponse:
    return MessageResponse(
        id=message_id,
        conversation_id=conversation_id,
        role="assistant",
        content="Shared response",
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )


# ---------------------------------------------------------------------------
# Owner endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_active_share_returns_null_when_unshared(client: AsyncClient):
    conv_id = await _seed_conversation()
    resp = await client.get(f"/api/conversations/{conv_id}/share")
    assert resp.status_code == 200
    assert resp.json() is None


@pytest.mark.asyncio
async def test_create_share_is_idempotent(client: AsyncClient):
    conv_id = await _seed_conversation()

    first = await client.post(f"/api/conversations/{conv_id}/share")
    assert first.status_code == 200
    token1 = first.json()["share_token"]
    assert isinstance(token1, str) and len(token1) > 10

    second = await client.post(f"/api/conversations/{conv_id}/share")
    assert second.status_code == 200
    # Same active row → same token. No churn for owners reopening the dialog.
    assert second.json()["share_token"] == token1


@pytest.mark.asyncio
async def test_revoke_share_invalidates_token(client: AsyncClient):
    conv_id = await _seed_conversation()
    create = await client.post(f"/api/conversations/{conv_id}/share")
    token = create.json()["share_token"]

    revoke = await client.delete(f"/api/conversations/{conv_id}/share")
    assert revoke.status_code == 204

    public = await client.get(f"/api/shares/{token}")
    assert public.status_code == 404


@pytest.mark.asyncio
async def test_revoke_then_create_issues_new_token(client: AsyncClient):
    conv_id = await _seed_conversation()
    first = (await client.post(f"/api/conversations/{conv_id}/share")).json()["share_token"]

    revoke = await client.delete(f"/api/conversations/{conv_id}/share")
    assert revoke.status_code == 204

    second = (await client.post(f"/api/conversations/{conv_id}/share")).json()["share_token"]
    assert second != first


@pytest.mark.asyncio
async def test_share_other_users_conversation_returns_404(client: AsyncClient):
    other_user_id = uuid.uuid4()
    conv_id = await _seed_conversation(owner_id=other_user_id)

    create = await client.post(f"/api/conversations/{conv_id}/share")
    # Existence is hidden — returning 404 (not 403) so the caller can't
    # enumerate conversation ids.
    assert create.status_code == 404


# ---------------------------------------------------------------------------
# Public visitor endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_public_share_view_returns_snapshot(client: AsyncClient):
    conv_id = await _seed_conversation()
    create = await client.post(f"/api/conversations/{conv_id}/share")
    token = create.json()["share_token"]

    # ``list_messages_from_checkpointer`` walks LangGraph state. Mocking it
    # with an empty list keeps the test scope on the share routing surface.
    with patch(
        "app.routers.shares.chat_service.list_messages_from_checkpointer",
        new=AsyncMock(return_value=[]),
    ):
        resp = await client.get(f"/api/shares/{token}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["share_token"] == token
    assert body["conversation_title"] == "Shared topic"
    assert body["agent"]["name"] == "Share Agent"
    assert body["agent"]["description"] == "An agent for share tests"
    assert body["messages"] == []
    # W6: trace 영속화 이전 대화는 빈 배열
    assert body["traces"] == []


@pytest.mark.asyncio
async def test_public_share_view_includes_persisted_traces(client: AsyncClient):
    """W6 — record_turn으로 시드된 trace가 share 응답에 포함된다."""
    from app.services import trace_storage

    conv_id = await _seed_conversation()
    create = await client.post(f"/api/conversations/{conv_id}/share")
    token = create.json()["share_token"]

    visible_message_id = uuid.uuid4()
    msg_id = str(visible_message_id)
    events = [
        {"id": f"{msg_id}-1", "event": "message_start", "data": {"id": msg_id}},
        {
            "id": f"{msg_id}-2",
            "event": "tool_call_start",
            "data": {"tool_name": "web_search", "parameters": {"q": "moldy"}},
        },
        {
            "id": f"{msg_id}-3",
            "event": "tool_call_result",
            "data": {"tool_name": "web_search", "result": "..."},
        },
        {
            "id": f"{msg_id}-4",
            "event": "message_end",
            "data": {"usage": {}, "content": "result"},
        },
    ]
    async with TestSession() as db:
        await trace_storage.record_turn(db, conversation_id=conv_id, events=events)
        await db.commit()

    with patch(
        "app.routers.shares.chat_service.list_messages_from_checkpointer",
        new=AsyncMock(return_value=[_shared_assistant_message(conv_id, visible_message_id)]),
    ):
        resp = await client.get(f"/api/shares/{token}")
    body = resp.json()
    assert len(body["traces"]) == 1
    trace = body["traces"][0]
    assert trace["assistant_msg_id"] == msg_id
    # 도구 호출 chip 추출에 필요한 모든 이벤트가 그대로 노출
    assert [e["event"] for e in trace["events"]] == [
        "message_start",
        "tool_call_start",
        "tool_call_result",
        "message_end",
    ]
    assert trace["events"][1]["data"]["tool_name"] == "web_search"


@pytest.mark.asyncio
async def test_public_share_view_includes_protocol_traces(client: AsyncClient):
    from app.services import trace_storage

    conv_id = await _seed_conversation()
    token = (await client.post(f"/api/conversations/{conv_id}/share")).json()["share_token"]

    visible_message_id = uuid.uuid4()
    run_id = str(visible_message_id)
    async with TestSession() as db:
        await trace_storage.append_events(
            db,
            conversation_id=conv_id,
            assistant_msg_id=run_id,
            events_chunk=[
                dict(
                    stored_protocol_event(
                        run_id=run_id,
                        thread_id=str(conv_id),
                        seq=1,
                        method="tools",
                        data={
                            "event": "tool-started",
                            "tool_call_id": "call-1",
                            "name": "web_search",
                            "args": {"query": "moldy"},
                        },
                    )
                )
            ],
            status="completed",
        )
        await db.commit()

    with patch(
        "app.routers.shares.chat_service.list_messages_from_checkpointer",
        new=AsyncMock(return_value=[_shared_assistant_message(conv_id, visible_message_id)]),
    ):
        resp = await client.get(f"/api/shares/{token}")

    assert resp.status_code == 200
    trace = resp.json()["traces"][0]
    assert trace["events"][0]["method"] == "tools"
    assert trace["events"][0]["data"]["args"] == {"query": "moldy"}


@pytest.mark.asyncio
async def test_public_share_projects_legacy_offload_traces_without_mutating_storage(
    client: AsyncClient,
) -> None:
    """The anonymous share surface exposes only logical offload IDs."""
    from app.services import trace_storage

    conv_id = await _seed_conversation()
    token = (await client.post(f"/api/conversations/{conv_id}/share")).json()["share_token"]
    visible_message_id = uuid.uuid4()
    message_id = str(visible_message_id)
    configured_secret = "share-configured-secret-42"
    history_path = "/conversation_history/session_0123456789abcdef0123456789abcdef.md"
    spill_path = "/large_tool_results/tool-call.json"
    virtual_path = (
        "/.moldy-offload/owner/conversation/actor/"
        "conversation_history/session_0123456789abcdef0123456789abcdef.md"
    )
    physical_path = (
        "/tmp/moldy/.moldy-internal/offload/spill/owner/run/actor/large_tool_results/tool-call.json"
    )
    events = [
        {
            "id": f"{message_id}-1",
            "event": "message_start",
            "data": {
                "id": message_id,
                "history": history_path,
                "spill": spill_path,
                "virtual": virtual_path,
                "physical": physical_path,
                "note": f"path={history_path}; credential={configured_secret}",
                "_summarization_event": {"file_path": history_path},
            },
        },
        {
            "id": f"{message_id}-2",
            "event": "memory_proposed",
            "data": {
                "id": "proposal-share-1",
                "scope": "user",
                "content": "share-private-memory-body",
                "reason": "share-private-memory-reason",
            },
        },
    ]
    async with TestSession() as db:
        await trace_storage.record_turn(db, conversation_id=conv_id, events=events)
        await db.commit()

    with (
        patch(
            "app.routers.shares.chat_service.list_messages_from_checkpointer",
            new=AsyncMock(return_value=[_shared_assistant_message(conv_id, visible_message_id)]),
        ),
        patch(
            "app.services.chat.secrets.collect_conversation_secret_values",
            new=AsyncMock(return_value={configured_secret}),
        ),
    ):
        response = await client.get(f"/api/shares/{token}")
        cached_response = await client.get(f"/api/shares/{token}")

    assert response.status_code == 200
    rendered = repr(response.json())
    assert cached_response.status_code == 200
    assert cached_response.json() == response.json()
    assert configured_secret not in rendered
    assert configured_secret not in repr(cached_response.json())
    for path in (history_path, spill_path, virtual_path, physical_path):
        assert path not in rendered
    assert "/conversation_history/" not in rendered
    assert "/large_tool_results/" not in rendered
    assert "/.moldy-offload/" not in rendered
    assert "/.moldy-internal/offload/" not in rendered
    assert "history_" in rendered
    assert "spill_" not in rendered
    assert "internal_reference_redacted" in rendered
    assert "file_path" not in rendered
    assert "share-private-memory-body" not in rendered
    assert "share-private-memory-reason" not in rendered

    async with TestSession() as db:
        stored = await db.scalar(
            select(MessageEvent).where(MessageEvent.assistant_msg_id == message_id)
        )
        assert stored is not None
        assert stored.events == events


@pytest.mark.asyncio
async def test_public_share_view_hides_traces_outside_shared_snapshot(client: AsyncClient):
    from app.services import trace_storage

    conv_id = await _seed_conversation()
    token = (await client.post(f"/api/conversations/{conv_id}/share")).json()["share_token"]

    visible_message_id = uuid.uuid4()
    hidden_message_id = uuid.uuid4()
    visible_events = [
        {
            "id": f"{visible_message_id}-1",
            "event": "message_start",
            "data": {"id": str(visible_message_id)},
        },
        {
            "id": f"{visible_message_id}-2",
            "event": "message_end",
            "data": {"content": "visible"},
        },
    ]
    hidden_events = [
        {
            "id": f"{hidden_message_id}-1",
            "event": "message_start",
            "data": {"id": str(hidden_message_id)},
        },
        {
            "id": f"{hidden_message_id}-2",
            "event": "tool_call_result",
            "data": {"result": "hidden branch secret"},
        },
        {
            "id": f"{hidden_message_id}-3",
            "event": "message_end",
            "data": {"content": "hidden"},
        },
    ]
    async with TestSession() as db:
        await trace_storage.record_turn(db, conversation_id=conv_id, events=visible_events)
        await trace_storage.record_turn(db, conversation_id=conv_id, events=hidden_events)
        await db.commit()

    with patch(
        "app.routers.shares.chat_service.list_messages_from_checkpointer",
        new=AsyncMock(return_value=[_shared_assistant_message(conv_id, visible_message_id)]),
    ):
        resp = await client.get(f"/api/shares/{token}")

    assert resp.status_code == 200
    traces = resp.json()["traces"]
    assert [trace["assistant_msg_id"] for trace in traces] == [str(visible_message_id)]
    assert "hidden branch secret" not in resp.text


@pytest.mark.asyncio
async def test_public_share_view_unknown_token_returns_404(client: AsyncClient):
    resp = await client.get("/api/shares/this-token-does-not-exist")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_public_share_messages_envelope(client: AsyncClient):
    conv_id = await _seed_conversation()
    token = (await client.post(f"/api/conversations/{conv_id}/share")).json()["share_token"]

    with patch(
        "app.routers.shares.chat_service.list_messages_from_checkpointer",
        new=AsyncMock(return_value=[]),
    ):
        resp = await client.get(f"/api/shares/{token}/messages")
    assert resp.status_code == 200
    body = resp.json()
    assert body["messages"] == []
    assert "active_checkpoint_id" in body
