"""Restore a recorded child decision through the authenticated PostgreSQL snapshot route."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from langgraph.checkpoint.base import copy_checkpoint
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import delete, select

from app.agent_runtime import checkpointer
from app.models.agent_subagent import AgentSubAgentLink
from app.models.approval_request import ApprovalRequest
from app.models.conversation import Conversation
from tests.integration.ag_ui_legacy_checks import OldRun, assert_standard_resume
from tests.integration.test_ag_ui_archived_child_approval import old_run as old_run
from tests.test_ag_ui_read_pending_native_legacy import _reader_client


async def _snapshot(client: AsyncClient, thread: UUID) -> dict[str, Any]:
    response = await client.get(f"/api/conversations/{thread}/ag-ui/snapshot")
    assert response.status_code == 200, response.text
    assert "no-store" in response.headers["Cache-Control"]
    return response.json()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("old_run", [("retained", "recorded")], indirect=True)
async def test_old_child_badge_tracks_actual_consumption_selected_branch_and_live_grant(
    old_run: OldRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = old_run
    async with (
        AsyncPostgresSaver.from_conn_string(case.dsn) as saver,
        _reader_client(case, monkeypatch) as client,
    ):
        monkeypatch.setattr(checkpointer, "_checkpointer", saver)
        before = await _snapshot(client, case.conversation_id)
        assert before["approvalDecisions"] == []
        [slot] = before["interrupts"]
        iid, owner = slot["id"], slot["subagentRunId"]
        assert iid == case.issued["interruptId"] and owner
        [(message_id, public_call_id)] = [
            (message["id"], call["id"])
            for message in before["messages"]
            if message.get("subagentRunId") == owner
            for call in message.get("toolCalls") or ()
            if call["function"]["name"] == "record_review"
        ]

        with monkeypatch.context() as submit:
            # The actual .42 route claims the .32 inbox row and resumes the real graph.
            await assert_standard_resume(case, False, submit)
        async with case.db() as db:
            claim = await db.scalar(
                select(ApprovalRequest).where(
                    ApprovalRequest.run_id == case.run_id, ApprovalRequest.interrupt_id == iid
                )
            )
            assert claim and claim.decisions == ["approve"] and claim.decision_no == 1
            assert claim.resumed_run_id and claim.resumed_run_id != case.run_id
        badge = {
            "messageId": message_id,
            "toolCallId": public_call_id,
            "physicalRunId": str(case.run_id),
            "interruptId": iid,
            "actionIndex": 0,
            "decision": "approve",
            "subagentRunId": owner,
        }
        for _ in range(2):
            restored = await _snapshot(client, case.conversation_id)
            assert restored["interrupts"] == []
            assert restored["approvalDecisions"] == [badge]

        async with case.db.begin() as writer:
            await writer.execute(
                delete(AgentSubAgentLink).where(
                    AgentSubAgentLink.parent_agent_id == case.agent_id,
                    AgentSubAgentLink.sub_agent_id == case.child_id,
                )
            )
        assert (await _snapshot(client, case.conversation_id))["approvalDecisions"] == []

        async with case.db.begin() as writer:
            writer.add(
                AgentSubAgentLink(
                    parent_agent_id=case.agent_id,
                    sub_agent_id=case.child_id,
                    delegated_by=case.user_id,
                )
            )
        assert (await _snapshot(client, case.conversation_id))["approvalDecisions"] == [badge]

        # Select a sibling of the original pending checkpoint that never consumed its decision.
        issuing = await saver.aget_tuple(
            {
                "configurable": {
                    "thread_id": str(case.conversation_id),
                    "checkpoint_ns": "",
                    "checkpoint_id": case.issued["rootCheckpointId"],
                }
            }
        )
        assert issuing and issuing.parent_config
        ancestor = await saver.aget_tuple(issuing.parent_config)
        assert ancestor
        sibling = copy_checkpoint(ancestor.checkpoint)
        sibling["id"] = str(UUID(int=UUID(issuing.checkpoint["id"]).int - 1))
        assert ancestor.checkpoint["id"] < sibling["id"] < issuing.checkpoint["id"]
        await saver.aput(ancestor.config, sibling, ancestor.metadata, {})
        async with case.db.begin() as writer:
            conversation = await writer.get(Conversation, case.conversation_id)
            assert conversation
            conversation.active_branch_checkpoint_id = sibling["id"]
        assert (await _snapshot(client, case.conversation_id))["approvalDecisions"] == []
