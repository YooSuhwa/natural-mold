"""An accepted approval survives a lost launch and reaches its interrupted graph once."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from ag_ui.core import RunAgentInput
from langgraph.types import Command
from sqlalchemy import func, select
from starlette.requests import Request

from app.agent_runtime import checkpointer, langgraph_agent_stream_runner
from app.agent_runtime.runtime_config import AgentConfig
from app.approvals import service as approvals
from app.config import settings
from app.models.ag_ui_command_receipt import AgUiCommandReceipt
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.services import ag_ui_deferred_delivery as delivery
from app.services import (
    conversation_run_queue_worker,
    conversation_run_worker,
    conversation_stream_service,
)
from app.services.ag_ui_submission import submit_ag_ui_run
from app.services.conversation_run_task_registry import RunTaskRegistry
from app.tenancy.checkpoint_audit import run_configurable
from tests.conftest import TestSession, override_get_current_user
from tests.test_ag_ui_pending_checkpoint import PausedChat, ask
from tests.test_ag_ui_pending_checkpoint import chat as chat


async def test_recovery_delivers_one_confirmed_approval_to_its_native_graph(
    chat: PausedChat, monkeypatch: pytest.MonkeyPatch
) -> None:
    for module in (
        conversation_run_queue_worker,
        conversation_run_worker,
        conversation_stream_service,
    ):
        monkeypatch.setattr(module, "async_session", TestSession)
    monkeypatch.setattr(checkpointer, "_checkpointer", chat.saver)
    monkeypatch.setattr(settings, "prepared_run_lease_enabled", False)
    payload = {
        "action_requests": [{"name": "review_note", "args": {"body": "Fixture note"}}],
        "review_configs": [{"action_name": "review_note", "allowed_decisions": ["approve"]}],
    }
    graph = chat.graph(review=ask(payload))
    paused = await graph.ainvoke({"answers": []}, chat.config())
    (slot,) = paused["__interrupt__"]
    await chat.set_reported_interrupt(slot.id)
    (approval,) = await approvals.record_pending(
        chat.db,
        chat.run,
        [approvals.PendingInterrupt(slot.id, payload)],
        origin=approvals.RunOrigin(),
    )
    await chat.db.commit()
    answer = {"decisions": [{"type": "approve"}]}
    command = RunAgentInput.model_validate(
        {
            "threadId": str(chat.conversation.id),
            "runId": "durable-approval",
            "protocolVersion": "1.0",
            "messages": [],
            "resume": [{"interruptId": slot.id, "status": "resolved", "payload": answer}],
        }
    )
    with monkeypatch.context() as before_crash:
        lost_launch = AsyncMock()
        before_crash.setattr(delivery, "dispatch_ag_ui_run", lost_launch)
        accepted = await submit_ag_ui_run(
            chat.db,
            request=Request({"type": "http", "headers": []}),
            context=chat.context,
            user=await override_get_current_user(),
            conversation=chat.conversation,
            run_input=command,
        )
        assert accepted.run_id is not None
        lost_launch.assert_awaited_once_with(accepted.run_id)

    await chat.db.refresh(approval)
    assert (approval.status, approval.decisions, approval.decision_no, approval.resumed_run_id) == (
        "approved",
        ["approve"],
        1,
        accepted.run_id,
    )
    bound_run = await chat.db.get(ConversationRun, accepted.run_id)
    assert bound_run is not None and (bound_run.status, bound_run.parent_run_id) == (
        "queued",
        chat.run.id,
    )
    encrypted = select(AgUiCommandReceipt.delivery_encrypted).where(
        AgUiCommandReceipt.run_id == accepted.run_id
    )
    assert await chat.db.scalar(encrypted)
    assert await chat.db.scalar(select(func.count(ConversationRunInput.id))) == 0
    await chat.db.commit()

    deliveries: list[tuple[UUID, object]] = []

    async def resume_graph(
        cfg: AgentConfig, *, stream_input: Command, run_id: str, moldy_source: str, **_kwargs: Any
    ) -> AsyncIterator[str]:
        assert moldy_source == "resume" and cfg.checkpoint_id == accepted.checkpoint_id
        deliveries.append((UUID(run_id), stream_input.resume))
        configurable = run_configurable(cfg.thread_id, UUID(run_id), chat.context.tenant_id)
        configurable["checkpoint_id"] = cfg.checkpoint_id
        resumed = await graph.ainvoke(stream_input, {"configurable": configurable})
        assert resumed["answers"] == [answer] and not resumed.get("__interrupt__")
        yield 'event: message_end\ndata: {"content":"Approved","usage":{}}\n\n'

    monkeypatch.setattr(langgraph_agent_stream_runner, "_run_langgraph_agent_stream", resume_graph)
    registry = RunTaskRegistry(worker_instance_id="approval-recovery")
    try:
        assert await delivery.recover_ag_ui_deliveries(registry=registry) == 1
        worker = registry.get(accepted.run_id)
        assert worker is not None
        await asyncio.wait_for(worker, timeout=15)
        assert await delivery.recover_ag_ui_deliveries(registry=registry) == 0
    finally:
        await registry.shutdown()
    assert deliveries == [(accepted.run_id, {slot.id: answer})]
    await chat.db.refresh(bound_run)
    assert bound_run.status == "completed"
    assert (await graph.aget_state(chat.config())).values["answers"] == [answer]
    assert (
        await chat.db.scalar(
            select(func.count(ConversationRun.id)).where(
                ConversationRun.parent_run_id == chat.run.id
            )
        )
        == 1
    )
