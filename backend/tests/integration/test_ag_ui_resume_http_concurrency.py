"""Two authenticated callers answer the same native approval under the same public run ID."""

import asyncio
import os
from collections.abc import Mapping
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from app.agent_runtime import checkpointer
from app.agent_runtime.langgraph_pending_inputs import pending_input_requested_events
from app.agent_runtime.protocol_persistence import persistable_protocol_event
from app.approvals import service as approvals
from app.models.ag_ui_command_receipt import AgUiCommandReceipt
from app.models.approval_request import ApprovalRequest
from app.models.conversation_run import ConversationRun
from app.models.organization import Tenant
from app.routers import conversation_ag_ui_commands
from app.services import ag_ui_deferred_delivery, conversation_run_service, trace_storage
from app.tenancy.checkpoint_audit import run_configurable
from tests.authz.unit_scope_fixture import unit_context
from tests.integration.test_chat_steer_concurrency import ActiveChat, active_chat
from tests.test_ag_ui_pending_checkpoint import Answers, ask

__all__ = ["active_chat"]


@pytest.mark.parametrize("second_vote", ["approve", "reject"])
async def test_two_resumes_using_one_public_id_claim_one_decision(
    active_chat: ActiveChat, monkeypatch: pytest.MonkeyPatch, second_vote: str
) -> None:
    chat = active_chat
    scope = unit_context(chat.user_id)
    payload = {
        "action_requests": [{"name": "review_note", "args": {"text": "A note"}}],
        "review_configs": [
            {"action_name": "review_note", "allowed_decisions": ["approve", "reject"]}
        ],
    }
    dsn = make_url(os.environ["INTEGRATION_DATABASE_URL"]).set(drivername="postgresql")
    async with AsyncPostgresSaver.from_conn_string(
        dsn.render_as_string(hide_password=False)
    ) as saver:
        await saver.setup()
        monkeypatch.setattr(checkpointer, "_checkpointer", saver)
        builder = StateGraph(Answers)
        builder.add_node("review", ask(payload)).add_edge(START, "review").add_edge("review", END)
        graph = builder.compile(checkpointer=saver)
        config = run_configurable(str(chat.conversation_id), chat.run_id, scope.tenant_id)
        paused = await graph.ainvoke({"answers": []}, {"configurable": config})
        (slot,) = paused["__interrupt__"]

        def continuation(vote: str) -> Mapping[str, Any]:
            return {
                "threadId": str(chat.conversation_id),
                "runId": "public-resume",
                "messages": [],
                "resume": [
                    {
                        "interruptId": slot.id,
                        "status": "resolved",
                        "payload": {"decisions": [{"type": vote}]},
                    }
                ],
            }

        # Observe acceptance before the physical worker starts. Only its wake and the
        # open-ended SSE attachment are stubbed; claims/receipts/checkpoints stay in PG.
        wake = AsyncMock()
        monkeypatch.setattr(ag_ui_deferred_delivery, "dispatch_ag_ui_run", wake)
        monkeypatch.setattr(
            conversation_ag_ui_commands,
            "ag_ui_run_response",
            AsyncMock(
                side_effect=lambda *args: StreamingResponse(
                    iter(()), media_type="text/event-stream"
                )
            ),
        )
        endpoint = f"/api/conversations/{chat.conversation_id}/ag-ui/runs"
        # A native interrupt can already be in the journal while its physical run is
        # still running. The user may submit immediately after receiving this event.
        (event,) = await pending_input_requested_events(
            graph,
            {"configurable": config},
            run_id=str(chat.run_id),
            thread_id=str(chat.conversation_id),
            emitted=[],
        )
        assert event["method"] == "input.requested" and event["data"]["interrupt_id"] == slot.id
        async with chat.sessions.begin() as db:
            await trace_storage.append_events(
                db,
                conversation_id=chat.conversation_id,
                assistant_msg_id=str(chat.run_id),
                events_chunk=[persistable_protocol_event(event)],
            )
        original_request = continuation("approve")
        early = await chat.client.post(endpoint, json=original_request)
        assert (early.status_code, early.json()["error"]["code"]) == (409, "STALE_INTERRUPT")
        wake.assert_not_awaited()
        async with chat.sessions() as db:
            # A failed submission released its transaction and left no decision behind.
            parent = await db.get(ConversationRun, chat.run_id, with_for_update={"nowait": True})
            assert parent is not None and parent.status == "running"
            assert (
                await db.scalar(
                    select(AgUiCommandReceipt.public_run_id).where(
                        AgUiCommandReceipt.conversation_id == chat.conversation_id
                    )
                )
                is None
            )
            assert (
                await db.scalar(
                    select(ConversationRun.id).where(ConversationRun.parent_run_id == chat.run_id)
                )
                is None
            )
            assert (
                await db.scalar(
                    select(ApprovalRequest.id).where(ApprovalRequest.run_id == chat.run_id)
                )
                is None
            )
            await conversation_run_service.transition_run(
                db, parent, "interrupted", interrupt_id=slot.id
            )
            await approvals.record_pending(
                db,
                parent,
                [approvals.PendingInterrupt(slot.id, payload)],
                origin=approvals.RunOrigin(),
            )
            await db.commit()

        votes = ["approve", second_vote]
        # Hold the first lock used by the execution gateway until both HTTP request
        # transactions are waiting. Even a fast local backend must encounter the race.
        async with (
            asyncio.timeout(30),
            asyncio.TaskGroup() as callers,
            chat.sessions.begin() as guard,
        ):
            await guard.get(Tenant, scope.tenant_id, with_for_update=True)
            tasks = [
                callers.create_task(chat.client.post(endpoint, json=body))
                for body in (original_request, continuation(second_vote))
            ]
            waiters = text("""
                WITH RECURSIVE blocked(pid) AS (
                    SELECT pg_backend_pid()
                    UNION
                    SELECT activity.pid FROM pg_stat_activity activity, blocked
                    WHERE activity.datname = current_database()
                      AND blocked.pid = ANY(pg_blocking_pids(activity.pid))
                )
                SELECT count(*) - 1 FROM blocked
            """)
            while await guard.scalar(waiters) != len(tasks):
                assert not any(task.done() for task in tasks)
                await guard.execute(text("SELECT pg_stat_clear_snapshot()"))
                await asyncio.sleep(0.01)
        responses = [task.result() for task in tasks]
        statuses = [response.status_code for response in responses]
        assert sorted(statuses) == ([200, 200] if second_vote == "approve" else [200, 409]), [
            response.text for response in responses
        ]
        accepted = votes[statuses.index(200)]
        if 409 in statuses:
            assert responses[statuses.index(409)].json()["error"]["code"] == "AG_UI_RUN_ID_CONFLICT"
        replay = await chat.client.post(endpoint, json=continuation(accepted))
        assert replay.status_code == 200, replay.text
        opposite = "reject" if accepted == "approve" else "approve"
        conflict = await chat.client.post(endpoint, json=continuation(opposite))
        assert (conflict.status_code, conflict.json()["error"]["code"]) == (
            409,
            "AG_UI_RUN_ID_CONFLICT",
        )

    async with chat.sessions() as db:
        receipt = (
            await db.scalars(
                select(AgUiCommandReceipt).where(
                    AgUiCommandReceipt.conversation_id == chat.conversation_id
                )
            )
        ).one()
        child = (
            await db.scalars(
                select(ConversationRun).where(ConversationRun.parent_run_id == chat.run_id)
            )
        ).one()
        approval = (
            await db.scalars(select(ApprovalRequest).where(ApprovalRequest.run_id == chat.run_id))
        ).one()
        assert receipt.run_id == child.id == approval.resumed_run_id
        assert (receipt.public_run_id, approval.decisions, approval.decision_no) == (
            "public-resume",
            [accepted],
            1,
        )
        wake.assert_awaited_once_with(child.id)
