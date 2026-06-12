from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import event_names
from app.agent_runtime.event_broker import registry as broker_registry
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_artifact import ConversationArtifact
from app.models.conversation_run import utc_now_naive
from app.models.message_event import MessageEvent
from app.models.model import Model
from app.models.user import User
from app.services import conversation_run_service, trace_storage
from tests.conftest import TEST_USER_ID


async def _seed_agent_conversation(
    db: AsyncSession,
    *,
    user_id: uuid.UUID = TEST_USER_ID,
    email: str = "run-router@test.local",
    title: str = "Router run",
) -> tuple[Agent, Conversation]:
    user = User(id=user_id, email=email, name="Run Router User")
    db.add(user)
    model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
    db.add(model)
    await db.flush()
    agent = Agent(
        user_id=user.id,
        name="Run Router Agent",
        system_prompt="You are helpful.",
        model_id=model.id,
    )
    db.add(agent)
    await db.flush()
    conversation = Conversation(agent_id=agent.id, title=title)
    db.add(conversation)
    await db.flush()
    return agent, conversation


@pytest.mark.asyncio
async def test_active_run_endpoint_returns_active_run(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    agent, conversation = await _seed_agent_conversation(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="active run",
    )
    await db.commit()

    resp = await client.get(f"/api/conversations/{conversation.id}/runs/active")

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(run.id)
    assert body["conversation_id"] == str(conversation.id)
    assert body["status"] == "queued"
    assert body["source"] == "chat"


@pytest.mark.asyncio
async def test_run_detail_returns_not_found_for_other_conversation(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    agent, first = await _seed_agent_conversation(db, title="first")
    second = Conversation(agent_id=agent.id, title="second")
    db.add(second)
    await db.flush()
    run = await conversation_run_service.create_run(
        db,
        conversation_id=first.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="first",
    )
    await db.commit()

    resp = await client.get(f"/api/conversations/{second.id}/runs/{run.id}")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_run_detail_returns_not_found_for_other_user(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    other_user_id = uuid.UUID("00000000-0000-0000-0000-000000000099")
    agent, conversation = await _seed_agent_conversation(
        db,
        user_id=other_user_id,
        email="other-run-router@test.local",
    )
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="hidden",
    )
    await db.commit()

    resp = await client.get(f"/api/conversations/{conversation.id}/runs/{run.id}")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_conversation_page_includes_active_run(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    agent, conversation = await _seed_agent_conversation(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="visible in list",
    )
    await db.commit()

    resp = await client.get(f"/api/agents/{agent.id}/conversations/page")

    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["id"] == str(conversation.id)
    assert item["active_run"]["id"] == str(run.id)
    assert item["active_run"]["status"] == "queued"


@pytest.mark.asyncio
async def test_messages_envelope_includes_active_run(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    agent, conversation = await _seed_agent_conversation(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="direct refresh",
    )
    await db.commit()

    resp = await client.get(f"/api/conversations/{conversation.id}/messages")

    assert resp.status_code == 200
    body = resp.json()
    assert body["messages"] == []
    assert body["active_run"]["id"] == str(run.id)
    assert body["latest_run"]["id"] == str(run.id)


@pytest.mark.asyncio
async def test_messages_envelope_reports_canceled_latest_run(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """취소된 run 은 active_run 에서 사라지지만 latest_run 으로는 보여야 한다.

    프론트가 refetch/새로고침 후에도 "중단됨" notice 를 durable 하게 렌더하는
    근거 데이터 — active_run 만 있으면 terminal 상태가 유실된다.
    """
    agent, conversation = await _seed_agent_conversation(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="will be canceled",
    )
    await conversation_run_service.transition_run(db, run, "canceling")
    await conversation_run_service.transition_run(db, run, "canceled")
    await db.commit()

    resp = await client.get(f"/api/conversations/{conversation.id}/messages")

    assert resp.status_code == 200
    body = resp.json()
    assert body["active_run"] is None
    assert body["latest_run"]["id"] == str(run.id)
    assert body["latest_run"]["status"] == "canceled"


@pytest.mark.asyncio
async def test_messages_envelope_latest_run_prefers_newest(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """취소 이후 새 turn 이 완료되면 latest_run 은 최신 run 을 가리킨다."""
    agent, conversation = await _seed_agent_conversation(db)
    canceled = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="first turn",
    )
    await conversation_run_service.transition_run(db, canceled, "canceling")
    await conversation_run_service.transition_run(db, canceled, "canceled")
    completed = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="second turn",
    )
    completed.created_at = canceled.created_at + timedelta(seconds=1)
    await conversation_run_service.transition_run(db, completed, "running")
    await conversation_run_service.transition_run(db, completed, "completed")
    await db.commit()

    resp = await client.get(f"/api/conversations/{conversation.id}/messages")

    assert resp.status_code == 200
    body = resp.json()
    assert body["latest_run"]["id"] == str(completed.id)
    assert body["latest_run"]["status"] == "completed"


@pytest.mark.asyncio
async def test_conversation_page_includes_latest_interrupted_run_for_action_required(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    agent, conversation = await _seed_agent_conversation(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="needs action",
    )
    await conversation_run_service.transition_run(db, run, "running", worker_instance_id="test")
    await conversation_run_service.transition_run(db, run, "interrupted", interrupt_id="hitl-1")
    await db.commit()

    resp = await client.get(f"/api/agents/{agent.id}/conversations/page")

    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["id"] == str(conversation.id)
    assert item["active_run"]["id"] == str(run.id)
    assert item["active_run"]["status"] == "interrupted"


@pytest.mark.asyncio
async def test_messages_envelope_includes_interrupted_run_for_action_required(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    agent, conversation = await _seed_agent_conversation(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="needs action",
    )
    await conversation_run_service.transition_run(db, run, "running", worker_instance_id="test")
    await conversation_run_service.transition_run(db, run, "interrupted", interrupt_id="hitl-1")
    await db.commit()

    resp = await client.get(f"/api/conversations/{conversation.id}/messages")

    assert resp.status_code == 200
    body = resp.json()
    assert body["active_run"]["id"] == str(run.id)
    assert body["active_run"]["status"] == "interrupted"


@pytest.mark.asyncio
async def test_run_stream_endpoint_attaches_live_broker(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    agent, conversation = await _seed_agent_conversation(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="live attach",
    )
    await conversation_run_service.transition_run(db, run, "running", worker_instance_id="test")
    await db.commit()

    broker = broker_registry.get_or_create(str(run.id), conversation_id=str(conversation.id))
    broker.publish_nowait(
        {
            "id": f"{run.id}-1",
            "event": event_names.MESSAGE_START,
            "data": {"id": str(run.id), "role": "assistant"},
        }
    )

    async def close_broker() -> None:
        await asyncio.sleep(0.01)
        broker.close()

    close_task = asyncio.create_task(close_broker())
    resp = await client.get(f"/api/conversations/{conversation.id}/runs/{run.id}/stream")
    await close_task

    assert resp.status_code == 200
    assert resp.headers["x-resume-mode"] == "live"
    assert "message_start" in resp.text


@pytest.mark.asyncio
async def test_run_stream_endpoint_replays_terminal_run(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    agent, conversation = await _seed_agent_conversation(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="replay",
    )
    await conversation_run_service.transition_run(db, run, "running", worker_instance_id="test")
    await conversation_run_service.transition_run(db, run, "completed")
    db.add(
        MessageEvent(
            conversation_id=conversation.id,
            assistant_msg_id=str(run.id),
            events=[
                {
                    "id": f"{run.id}-1",
                    "event": event_names.MESSAGE_END,
                    "data": {"content": "done", "usage": {}},
                }
            ],
            last_event_id=f"{run.id}-1",
            status="completed",
        )
    )
    await db.commit()

    resp = await client.get(f"/api/conversations/{conversation.id}/runs/{run.id}/stream")

    assert resp.status_code == 200
    assert resp.headers["x-resume-mode"] == "replay"
    assert "done" in resp.text


@pytest.mark.asyncio
async def test_ag_ui_run_stream_endpoint_attaches_live_broker_with_split_event_resume(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    agent, conversation = await _seed_agent_conversation(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="ag ui live attach",
    )
    await conversation_run_service.transition_run(db, run, "running", worker_instance_id="test")
    await db.commit()

    broker = broker_registry.get_or_create(str(run.id), conversation_id=str(conversation.id))
    broker.publish_nowait(
        {
            "id": f"{run.id}-1",
            "event": event_names.MESSAGE_START,
            "data": {"id": str(run.id), "role": "assistant"},
        }
    )

    async def close_broker() -> None:
        await asyncio.sleep(0.01)
        broker.close()

    close_task = asyncio.create_task(close_broker())
    resp = await client.get(
        f"/api/conversations/{conversation.id}/runs/{run.id}/ag-ui-stream",
        params={"last_event_id": f"{run.id}-1:ag:0"},
    )
    await close_task

    assert resp.status_code == 200
    assert resp.headers["x-resume-mode"] == "live"
    assert resp.headers["x-stream-protocol"] == "ag_ui"
    assert "RUN_STARTED" not in resp.text
    assert "TEXT_MESSAGE_START" in resp.text


@pytest.mark.asyncio
async def test_run_stream_emits_stale_gap_marker_when_last_event_evicted(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """last_event_id 가 ring buffer 에서 evict 되면 silent gap 대신 stale 마커 +
    buffer 잔여분 replay 로 degrade 한다."""
    agent, conversation = await _seed_agent_conversation(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="gap",
    )
    await conversation_run_service.transition_run(db, run, "running", worker_instance_id="test")
    await db.commit()

    broker = broker_registry.get_or_create(
        str(run.id),
        conversation_id=str(conversation.id),
        buffer_size=2,
    )
    broker.publish_nowait(
        {
            "id": f"{run.id}-1",
            "event": event_names.MESSAGE_START,
            "data": {"id": str(run.id), "role": "assistant"},
        }
    )
    broker.publish_nowait(
        {
            "id": f"{run.id}-2",
            "event": event_names.CONTENT_DELTA,
            "data": {"delta": "first"},
        }
    )
    # buffer_size=2 — 세 번째 publish 로 event 1 이 evict 된다.
    broker.publish_nowait(
        {
            "id": f"{run.id}-3",
            "event": event_names.CONTENT_DELTA,
            "data": {"delta": "second"},
        }
    )

    async def close_broker() -> None:
        await asyncio.sleep(0.01)
        broker.close()

    close_task = asyncio.create_task(close_broker())
    resp = await client.get(
        f"/api/conversations/{conversation.id}/runs/{run.id}/stream",
        params={"last_event_id": f"{run.id}-1"},
    )
    await close_task

    assert resp.status_code == 200
    assert resp.headers["x-resume-mode"] == "live"
    assert "broker_gap" in resp.text
    # buffer 에 남은 두 이벤트는 정상 replay
    assert "first" in resp.text
    assert "second" in resp.text


@pytest.mark.asyncio
async def test_ag_ui_run_stream_emits_stale_gap_marker_when_source_evicted(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    agent, conversation = await _seed_agent_conversation(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="ag ui gap",
    )
    await conversation_run_service.transition_run(db, run, "running", worker_instance_id="test")
    await db.commit()

    broker = broker_registry.get_or_create(
        str(run.id),
        conversation_id=str(conversation.id),
        buffer_size=2,
    )
    broker.publish_nowait(
        {
            "id": f"{run.id}-1",
            "event": event_names.MESSAGE_START,
            "data": {"id": str(run.id), "role": "assistant"},
        }
    )
    broker.publish_nowait(
        {
            "id": f"{run.id}-2",
            "event": event_names.CONTENT_DELTA,
            "data": {"delta": "first"},
        }
    )
    broker.publish_nowait(
        {
            "id": f"{run.id}-3",
            "event": event_names.CONTENT_DELTA,
            "data": {"delta": "second"},
        }
    )

    async def close_broker() -> None:
        await asyncio.sleep(0.01)
        broker.close()

    close_task = asyncio.create_task(close_broker())
    resp = await client.get(
        f"/api/conversations/{conversation.id}/runs/{run.id}/ag-ui-stream",
        params={"last_event_id": f"{run.id}-1:ag:0"},
    )
    await close_task

    assert resp.status_code == 200
    assert resp.headers["x-resume-mode"] == "live"
    assert "moldy.stale" in resp.text
    assert "broker_gap" in resp.text
    assert "TEXT_MESSAGE_CONTENT" in resp.text


@pytest.mark.asyncio
async def test_ag_ui_run_stream_endpoint_replays_terminal_run_with_split_event_resume(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    agent, conversation = await _seed_agent_conversation(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="ag ui replay",
    )
    await conversation_run_service.transition_run(db, run, "running", worker_instance_id="test")
    await conversation_run_service.transition_run(db, run, "completed")
    db.add(
        MessageEvent(
            conversation_id=conversation.id,
            assistant_msg_id=str(run.id),
            events=[
                {
                    "id": f"{run.id}-1",
                    "event": event_names.MESSAGE_START,
                    "data": {"id": str(run.id), "role": "assistant"},
                },
                {
                    "id": f"{run.id}-2",
                    "event": event_names.CONTENT_DELTA,
                    "data": {"delta": "ag-ui replay"},
                },
                {
                    "id": f"{run.id}-3",
                    "event": event_names.MESSAGE_END,
                    "data": {"content": "ag-ui replay", "usage": {}, "status": "completed"},
                },
            ],
            last_event_id=f"{run.id}-3",
            status="completed",
        )
    )
    await db.commit()

    resp = await client.get(
        f"/api/conversations/{conversation.id}/runs/{run.id}/ag-ui-stream",
        params={"last_event_id": f"{run.id}-1:ag:0"},
    )

    assert resp.status_code == 200
    assert resp.headers["x-resume-mode"] == "replay"
    assert resp.headers["x-stream-protocol"] == "ag_ui"
    assert "RUN_STARTED" not in resp.text
    assert "TEXT_MESSAGE_START" in resp.text
    assert "TEXT_MESSAGE_CONTENT" in resp.text
    assert "RUN_FINISHED" in resp.text


@pytest.mark.asyncio
async def test_run_stream_endpoint_returns_retry_for_fresh_active_run_without_broker(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    agent, conversation = await _seed_agent_conversation(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="retry",
    )
    await conversation_run_service.transition_run(db, run, "running", worker_instance_id="test")
    await db.commit()

    resp = await client.get(f"/api/conversations/{conversation.id}/runs/{run.id}/stream")

    assert resp.status_code == 409
    assert resp.headers["retry-after"] == "1"
    assert resp.json()["error"]["details"]["code"] == "RUN_ATTACH_RETRY"


@pytest.mark.asyncio
async def test_run_stream_endpoint_marks_old_active_run_stale_without_broker(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routers import conversation_runs

    monkeypatch.setattr(conversation_runs.settings, "chat_run_stale_after_seconds", 1)
    agent, conversation = await _seed_agent_conversation(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="stale",
    )
    await conversation_run_service.transition_run(db, run, "running", worker_instance_id="test")
    run.heartbeat_at = run.created_at = utc_now_naive().replace(year=2000)
    await db.commit()

    resp = await client.get(f"/api/conversations/{conversation.id}/runs/{run.id}/stream")

    assert resp.status_code == 200
    assert resp.headers["x-resume-mode"] == "stale"
    assert "run_worker_lost" in resp.text
    await db.refresh(run)
    assert run.status == "stale"
    assert run.is_active is False


@pytest.mark.asyncio
async def test_cancel_endpoint_returns_existing_canceling_status_without_second_path(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    agent, conversation = await _seed_agent_conversation(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="already canceling",
    )
    await conversation_run_service.transition_run(db, run, "running", worker_instance_id="lost")
    await conversation_run_service.transition_run(db, run, "canceling")
    await db.commit()

    resp = await client.post(f"/api/conversations/{conversation.id}/runs/{run.id}/cancel")

    assert resp.status_code == 200
    assert resp.json()["status"] == "canceling"
    await db.refresh(run)
    assert run.status == "canceling"


@pytest.mark.asyncio
async def test_cancel_endpoint_finalizes_outputs_when_worker_task_is_missing(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    agent, conversation = await _seed_agent_conversation(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="lost worker cancel",
    )
    await conversation_run_service.transition_run(db, run, "running", worker_instance_id="lost")
    await trace_storage.append_events(
        db,
        conversation_id=conversation.id,
        assistant_msg_id=str(run.id),
        events_chunk=[
            {
                "id": f"{run.id}-start",
                "event": event_names.MESSAGE_START,
                "data": {"id": str(run.id), "role": "assistant"},
            }
        ],
        status="streaming",
    )
    db.add(
        ConversationArtifact(
            user_id=agent.user_id,
            agent_id=agent.id,
            conversation_id=conversation.id,
            assistant_msg_id=str(run.id),
            logical_path="report.md",
            display_name="report.md",
            mime_type="text/markdown",
            artifact_kind="markdown",
            size_bytes=12,
            sha256="a" * 64,
            status="ready",
        )
    )
    await db.commit()

    resp = await client.post(f"/api/conversations/{conversation.id}/runs/{run.id}/cancel")

    assert resp.status_code == 200
    assert resp.json()["status"] == "canceled"
    await db.refresh(run)
    assert run.status == "canceled"
    assert run.is_active is False

    record = await trace_storage.get_trace_by_msg_id(db, str(run.id))
    assert record is not None
    assert record.status == "completed"
    assert any(
        evt.get("event") == event_names.MESSAGE_END
        and (evt.get("data") or {}).get("status") == "canceled"
        for evt in record.events
    )

    artifact = (
        await db.execute(
            select(ConversationArtifact).where(ConversationArtifact.assistant_msg_id == str(run.id))
        )
    ).scalar_one()
    assert artifact.status == "failed"


@pytest.mark.asyncio
async def test_cancel_endpoint_returns_terminal_status_without_restarting_work(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    agent, conversation = await _seed_agent_conversation(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="already done",
    )
    await conversation_run_service.transition_run(db, run, "running", worker_instance_id="test")
    await conversation_run_service.transition_run(db, run, "completed")
    await db.commit()

    resp = await client.post(f"/api/conversations/{conversation.id}/runs/{run.id}/cancel")

    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"
    await db.refresh(run)
    assert run.status == "completed"
