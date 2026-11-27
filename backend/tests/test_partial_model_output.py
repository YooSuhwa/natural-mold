"""A partial turn is read-only display data and belongs to the selected branch."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from langchain_core.messages import HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.run_metrics import RunMetricsAccumulator
from app.agent_runtime.stream_retry_policy import PartialModelStreamError
from app.dependencies import CurrentUser
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun, utc_now_naive
from app.services import conversation_run_worker as worker
from app.services.partial_model_output import PARTIAL_OUTPUT_KEY, capture_partial_output
from app.services.thread_branch_service import _CheckpointSlim
from tests.authz.run_fixture_config import run_fixture_config
from tests.conftest import TEST_USER_ID, TestSession
from tests.test_conversation_agent_protocol_router import _seed_conversation
from tests.test_conversation_agent_protocol_state_hydration import _FakeCheckpointer
from tests.test_conversation_run_finishing_cancel import _queued_run


def _event(kind: str, **kwargs: Any) -> dict[str, Any]:
    return {"method": "messages", "namespace": [], "data": {"event": kind, **kwargs}}


def _delta(text: str) -> dict[str, Any]:
    return _event("content-block-delta", delta={"type": "text-delta", "text": text})


def test_project_only_unfinished_root_text_and_confirmed_tool_progress() -> None:
    events = [
        _event("message-start", role="ai"),
        _delta("already checkpointed"),
        _event(
            "content-block-start",
            content={"type": "tool_call_chunk", "id": "a", "name": "lookup", "args": "private"},
        ),
        _event("message-finish"),
        {
            "method": "tools",
            "namespace": [],
            "data": {
                "event": "tool-started",
                "tool_call_id": "a",
                "tool_name": "lookup",
                "input": "private",
            },
        },
        {
            "method": "tools",
            "namespace": [],
            "data": {"event": "tool-finished", "tool_call_id": "a", "output": "private"},
        },
        _event("message-start", role="ai"),
        _delta("answer unique-"),
        {**_delta("private"), "namespace": ["child"]},
        {**_delta("private"), "run_id": "another"},
        _event("content-block-delta", delta={"type": "reasoning-delta", "text": "private"}),
        _delta("credential"),
        _event(
            "content-block-start",
            content={"type": "tool_call_chunk", "id": "b", "name": "publish", "args": "private"},
        ),
    ]
    output = capture_partial_output(
        events, run_id="run", thread_id="thread", secret_values={"unique-credential"}
    )
    assert output.text.startswith("answer ") and "unique-" not in output.text
    assert [tool.model_dump() for tool in output.tools] == [
        {"name": "lookup", "state": "completed"},
        {"name": "publish", "state": "unknown"},
    ]
    assert "private" not in output.model_dump_json()
    assert not output.truncated


def test_display_limit_cannot_cut_through_a_run_secret() -> None:
    text = "x" * 16_380
    secret = "unique-credential-value"
    result = capture_partial_output(
        [_event("message-start", role="ai"), _delta(text + secret + "more")],
        run_id="run",
        thread_id="thread",
        secret_values=[secret],
    )
    assert result.truncated
    assert result.text == text


def test_legacy_partial_is_display_only_after_an_earlier_tool_result() -> None:
    events = [
        {"event": "content_delta", "data": {"delta": "done"}},
        {
            "event": "tool_call_result",
            "data": {"tool_name": "lookup", "tool_call_id": "a", "result": "private"},
        },
        {"event": "content_delta", "data": {"delta": "partial"}},
        {
            "event": "tool_call_start",
            "data": {"tool_name": "send", "tool_call_id": "b", "parameters": {"private": "x"}},
        },
    ]
    result = capture_partial_output(events, run_id="run", thread_id="thread", secret_values=[])
    assert result.text == "partial"
    assert [(tool.name, tool.state) for tool in result.tools] == [
        ("lookup", "completed"),
        ("send", "unknown"),
    ]
    assert "private" not in result.model_dump_json()


async def test_worker_saves_read_only_partial_and_restores_only_on_its_branch(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation_id, run_id = await _queued_run()
    registry = worker.RunTaskRegistry(worker_instance_id="partial-stream")
    sends = 0

    async def broken_stream(*_args: Any, **kwargs: Any) -> AsyncGenerator[str, None]:
        nonlocal sends
        sends += 1
        kwargs["trace_sink"].extend(
            [_event("message-start", role="ai"), _delta("visible TEST-"), _delta("redaction")]
        )
        raise PartialModelStreamError()
        yield ""  # pragma: no cover

    async def idle_heartbeat(*_args: Any) -> None:
        await asyncio.Event().wait()

    async def prepared_metrics(**kwargs: Any) -> RunMetricsAccumulator:
        return RunMetricsAccumulator(started_at=kwargs["started_at"])

    monkeypatch.setattr(worker, "_heartbeat_until_terminal", idle_heartbeat)
    monkeypatch.setattr(worker, "_prepare_run_metrics", prepared_metrics)
    cfg = await run_fixture_config(run_id, TestSession)
    cfg.secret_values.add("TEST-redaction")
    await worker.start_conversation_run(
        run_id=run_id,
        conversation_id=conversation_id,
        cfg=cfg,
        user=CurrentUser(id=TEST_USER_ID, email="owner@test", name="Owner"),
        input_payload={"messages": [{"role": "user", "content": "go"}]},
        moldy_source="chat",
        executor_fn=broken_stream,
        registry=registry,
    )
    owner = registry.get(run_id)
    assert owner is not None
    await asyncio.wait_for(owner, 10)
    row = await db.get(ConversationRun, run_id)
    assert (
        row is not None
        and row.status == "failed"
        and row.error_code == PartialModelStreamError.code
    )
    stored = (row.metadata_json or {})[PARTIAL_OUTPUT_KEY]
    assert stored["text"].startswith("visible ") and "TEST-" not in stored["text"]
    assert stored["tools"] == []
    parent = _CheckpointSlim("base", None, [HumanMessage(id="base", content="first")])
    own = _CheckpointSlim("own", "base", [*parent.messages, HumanMessage(id="own", content="go")])
    sibling = _CheckpointSlim(
        "sibling", "base", [*parent.messages, HumanMessage(id="sib", content="other")]
    )

    class BranchSaver(_FakeCheckpointer):
        async def aget_tuple(self, config: Any) -> Any:
            item = await super().aget_tuple(config)
            checkpoint_id = config["configurable"].get("checkpoint_id")
            item.metadata = {
                "moldy_run_id": str(run_id) if checkpoint_id == "own" else str(UUID(int=0))
            }
            return item

    saver = BranchSaver([own, sibling, parent])
    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol_state_snapshot.get_checkpointer", lambda: saver
    )
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    for selected in ("own", "sibling", "missing-old-branch", "own"):
        conversation.active_branch_checkpoint_id = selected
        await db.commit()
        response = await client.get(
            f"/api/conversations/{conversation_id}/langgraph/threads/{conversation_id}/state"
        )
        assert response.status_code == 200, response.text
        state = response.json()
        meta = state["values"]["__moldy_runtime"]["latest_run"]
        assert meta["id"] == str(run_id)
        assert meta == state["metadata"]["latest_run"]
        if selected == "own":
            assert meta["partial_output"] == stored
        else:
            assert "partial_output" not in meta
    detail = await client.get(f"/api/conversations/{conversation_id}/runs/{run_id}")
    assert detail.status_code == 200 and stored["text"] not in detail.text
    assert sends == 1


async def test_other_owners_cannot_hydrate_partial_data(
    client: AsyncClient, db: AsyncSession
) -> None:
    conversation = await _seed_conversation(db, user_id=uuid4())
    run = ConversationRun(
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        user_id=conversation.user_id,
        status="failed",
        source="chat",
        is_active=False,
        error_code=PartialModelStreamError.code,
        metadata_json={
            PARTIAL_OUTPUT_KEY: {"text": "private-partial", "tools": [], "truncated": False}
        },
    )
    db.add(run)
    await db.commit()
    response = await client.get(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/state"
    )
    assert response.status_code == 404
    assert "private-partial" not in response.text


@pytest.mark.parametrize("use_compat", [False, True])
async def test_recovery_read_leaves_a_newer_running_run_untouched(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch, use_compat: bool
) -> None:
    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol_stale.settings.chat_run_stale_after_seconds", 1
    )
    conversation = await _seed_conversation(db)
    old = utc_now_naive() - timedelta(minutes=10)
    row = ConversationRun(
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        user_id=conversation.user_id,
        status="running",
        source="chat",
        is_active=True,
        heartbeat_at=old,
        started_at=old,
        created_at=old,
    )
    db.add(row)
    await db.commit()
    suffix = "state" if use_compat else f"threads/{conversation.id}/state"
    response = await client.get(
        f"/api/conversations/{conversation.id}/langgraph/{suffix}?read_only=true"
    )
    assert response.status_code == 200, response.text
    await db.refresh(row)
    assert row.status == "running" and row.is_active


@pytest.mark.parametrize(
    "result", [{"status": "error"}, {"output": {"status": "error"}}, {"is_error": True}]
)
def test_failed_tool_is_never_reported_as_completed(result: dict[str, Any]) -> None:
    output = capture_partial_output(
        [
            {
                "method": "tools",
                "namespace": [],
                "data": {"event": "tool-started", "tool_call_id": "a", "tool_name": "send"},
            },
            {
                "method": "tools",
                "namespace": [],
                "data": {"event": "tool-finished", "tool_call_id": "a", **result},
            },
        ],
        run_id="run",
        thread_id="thread",
        secret_values=[],
    )
    assert [tool.state for tool in output.tools] == ["unknown"]


def test_truncation_cannot_expose_overlapping_secret_prefixes() -> None:
    prefix = "x" * (16_384 - 12)
    result = capture_partial_output(
        [_event("message-start", role="ai"), _delta(prefix + "ABCDEFGHIJKLMNO-more")],
        run_id="run",
        thread_id="thread",
        secret_values={"ABCDEFGHIJ", "IJKLMNO"},
    )
    assert result.truncated and result.text == prefix
