"""Historical child evidence remains readable only on its consumed approval's authorized path."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import delete, select, update

from app.agent_runtime import checkpointer
from app.agent_runtime.ag_ui_v1_issuance import public_tool_call_id
from app.auth.jwt import create_csrf_token
from app.config import settings
from app.models.ag_ui_command_receipt import AgUiCommandReceipt
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation_run import ConversationRun
from tests.integration.ag_ui_legacy_checks import OldRun, assert_standard_resume
from tests.integration.test_ag_ui_archived_child_approval import _archived_run
from tests.integration.test_ag_ui_archived_child_approval import old_run as old_run
from tests.test_ag_ui_read_pending_native_legacy import _read_public_views, _reader_client

pytestmark = pytest.mark.integration


def _issued_decision(case: OldRun, before: dict[str, Any]) -> dict[str, Any]:
    assert before["approvalDecisions"] == []
    [slot] = before["interrupts"]
    iid, owner = slot["id"], slot["subagentRunId"]
    assert iid == case.issued["interruptId"]
    [child] = [row for row in before["subagents"] if row["subagentRunId"] == owner]
    assert child["physicalRunId"] == str(case.run_id) and child["interruptIds"] == [iid]
    [(message_id, public_call)] = [
        (msg["id"], call["id"])
        for msg in before["messages"]
        if msg.get("subagentRunId") == owner
        for call in msg.get("toolCalls") or ()
        if call["function"]["name"] == "record_review"
    ]
    # review-fixture is the original tool call ID in the .32 archived child graph.
    assert public_call == public_tool_call_id(str(case.run_id), owner, message_id, "review-fixture")
    return {
        "messageId": message_id,
        "toolCallId": public_call,
        "physicalRunId": str(case.run_id),
        "interruptId": iid,
        "actionIndex": 0,
        "decision": "approve",
        "subagentRunId": owner,
    }


def _assert_consumed(
    case: OldRun,
    snapshot: dict[str, Any],
    frames: list[dict[str, Any]],
    expected_decision: dict[str, Any] | None = None,
) -> None:
    assert snapshot["runId"] == snapshot["selectedPhysicalRunId"] == str(case.run_id)
    assert snapshot["interrupts"] == []
    assert snapshot["interruptRunId"] is None and snapshot["physicalInterruptRunId"] is None
    if expected_decision is None:
        assert snapshot["approvalDecisions"] == []
    else:
        [decision] = snapshot["approvalDecisions"]
        assert decision == expected_decision
        # Only this exact field may retain the spent ID. Check every other public
        # field and frame, including nested cards, child slots and response schemas.
        snapshot = {**snapshot, "approvalDecisions": [{**decision, "interruptId": None}]}
    assert case.issued["interruptId"] not in json.dumps([snapshot, frames])
    assert all(
        not child.get("legacyInterruptAccess") and not child.get("interruptIds")
        for child in snapshot["subagents"]
    )


def _assert_child_hidden(
    case: OldRun, snapshot: dict[str, Any], frames: list[dict[str, Any]]
) -> None:
    _assert_consumed(case, snapshot, frames)
    assert not snapshot["subagents"]
    assert not any(msg.get("subagentRunId") for msg in snapshot["messages"])
    assert not any(
        call["function"]["name"] == "task"
        for msg in snapshot["messages"]
        for call in msg.get("toolCalls") or ()
    )
    text = json.dumps([snapshot, frames])
    for value in (case.child_name, case.issued["toolCallId"], "record_review", "Review fixture."):
        assert value not in text


async def _assert_history(case: OldRun, monkeypatch: pytest.MonkeyPatch, *, retained: bool) -> None:
    async with (
        AsyncPostgresSaver.from_conn_string(case.dsn) as saver,
        _reader_client(case, monkeypatch) as client,
    ):
        monkeypatch.setattr(checkpointer, "_checkpointer", saver)
        expected_decision = None
        if retained:
            before = await client.get(
                f"/api/conversations/{case.conversation_id}/ag-ui/snapshot",
                params={"runId": str(case.run_id)},
            )
            assert before.status_code == 200
            expected_decision = _issued_decision(case, before.json())
        with monkeypatch.context() as writer:
            # Posts the real standard command twice and verifies one completed native effect.
            await assert_standard_resume(case, revoked=False, monkeypatch=writer)
        async with case.db() as db:
            successor = (
                await db.scalars(
                    select(ConversationRun).where(ConversationRun.parent_run_id == case.run_id)
                )
            ).one()
            assert successor.status == "completed" and successor.source == "resume"
        root_config: RunnableConfig = {
            "configurable": {"thread_id": str(case.conversation_id), "checkpoint_ns": ""}
        }
        root = await saver.aget_tuple(root_config)
        assert root is not None and root.metadata.get("moldy_run_id") == str(successor.id)
        old_checkpoint = case.issued["rootCheckpointId"]

        snapshot, frames, allowed_cursor = await _read_public_views(client, case)
        _assert_consumed(case, snapshot, frames, expected_decision)
        # A new submission cannot decide the historical approval a second time.
        csrf = create_csrf_token(case.user_id)
        client.cookies.set(settings.cookie_name_csrf, csrf)
        again = await client.post(
            f"/api/conversations/{case.conversation_id}/ag-ui/runs",
            headers={"X-CSRF-Token": csrf},
            json={
                "threadId": str(case.conversation_id),
                "runId": uuid4().hex,
                "messages": [],
                "resume": [
                    {
                        "interruptId": case.issued["interruptId"],
                        "status": "resolved",
                        "payload": {"decisions": [{"type": "approve"}]},
                    }
                ],
            },
        )
        assert again.status_code == 409 and again.json()["error"]["code"] == "STALE_INTERRUPT"
        assert snapshot["latestPhysicalRunId"] == str(successor.id)
        [(issuer, public_call)] = [
            (message, call)
            for message in snapshot["messages"]
            for call in message.get("toolCalls") or ()
            if call["function"]["name"] == "task"
        ]
        assert public_call["id"] != case.issued["toolCallId"]
        if retained:
            [child] = [
                row for row in snapshot["subagents"] if row.get("physicalRunId") == str(case.run_id)
            ]
            assert child["parentMessageId"] == issuer["id"]
            assert child["parentToolCallId"] == public_call["id"]
            assert child["subagentRunId"] in json.dumps(frames)
            assert "record_review" in json.dumps(frames)
        else:
            # The native root proves the issuer, but it cannot supply missing child messages.
            assert not snapshot["subagents"] and not any(f.get("subagentRunId") for f in frames)

        async with case.db.begin() as db:
            receipt = (
                await db.scalars(
                    select(AgUiCommandReceipt).where(AgUiCommandReceipt.run_id == successor.id)
                )
            ).one()
            assert receipt.checkpoint_id == old_checkpoint
            # Keep the genuine successor, but remove the link to the approval it consumed.
            receipt.checkpoint_id = None
        hidden, unproven, _ = await _read_public_views(client, case)
        _assert_child_hidden(case, hidden, unproven)
        async with case.db.begin() as db:
            await db.execute(
                update(AgUiCommandReceipt)
                .where(AgUiCommandReceipt.run_id == successor.id)
                .values(checkpoint_id=old_checkpoint)
            )
            await db.execute(
                delete(AgentSubAgentLink).where(
                    AgentSubAgentLink.parent_agent_id == case.agent_id,
                    AgentSubAgentLink.sub_agent_id == case.child_id,
                )
            )
        revoked, denied, _ = await _read_public_views(client, case)
        _assert_child_hidden(case, revoked, denied)
        path = f"/api/conversations/{case.conversation_id}/ag-ui"
        params = {"runId": str(case.run_id)}
        page = await client.get(f"{path}/events", params={**params, "after": allowed_cursor})
        stream = await client.get(
            f"{path}/stream", params=params, headers={"Last-Event-ID": allowed_cursor}
        )
        for response in (page, stream):
            assert response.status_code == 409 and "AG_UI_SNAPSHOT_REQUIRED" in response.text
        after = await saver.aget_tuple(root_config)
        assert (
            after is not None
            and after.config == root.config
            and after.pending_writes == root.pending_writes
        )
        async with case.db() as db:
            assert list(
                await db.scalars(
                    select(ConversationRun.id).where(ConversationRun.parent_run_id == case.run_id)
                )
            ) == [successor.id]


@pytest.mark.asyncio
async def test_consumed_archived_checkpoint_has_no_actionable_old_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _archived_run() as case:
        await _assert_history(case, monkeypatch, retained=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("old_run", [("retained", "recorded")], indirect=True)
async def test_consumed_child_history_follows_original_receipt_and_live_grant(
    old_run: OldRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _assert_history(old_run, monkeypatch, retained=True)
