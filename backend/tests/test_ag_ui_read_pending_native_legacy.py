"""Read an archived Deep Agents 0.7.32 child approval without resuming the graph."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import checkpointer
from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.auth.jwt import create_access_token
from app.config import settings
from app.dependencies import get_db
from app.main import create_app
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.services import conversation_stream_service
from app.services.ag_ui_read_pending import read_proven_interrupts
from tests.authz.unit_scope_fixture import unit_context
from tests.integration.ag_ui_legacy_checks import OldRun
from tests.integration.ag_ui_legacy_live import old_run as old_run
from tests.integration.test_ag_ui_archived_child_approval import _archived_run

pytestmark = pytest.mark.integration


async def _read_public_views(
    client: AsyncClient, case: OldRun
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    path = f"/api/conversations/{case.conversation_id}/ag-ui"
    params = {"runId": str(case.run_id)}
    snapshot = await client.get(f"{path}/snapshot", params=params)
    page = await client.get(f"{path}/events", params={**params, "limit": "256"})
    stream = await client.get(f"{path}/stream", params=params)
    assert (snapshot.status_code, page.status_code, stream.status_code) == (200, 200, 200)
    strict = [
        json.loads(line.removeprefix("data: "))
        for line in stream.text.splitlines()
        if line.startswith("data: ")
    ]
    frames = page.json()["frames"]
    assert page.json()["terminal"] and strict == [frame["event"] for frame in frames]
    assert strict[0]["type"] == "RUN_STARTED"
    return snapshot.json(), strict, frames[-1]["id"]


@asynccontextmanager
async def _reader_client(
    case: OldRun, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    async def isolated_db() -> AsyncIterator[AsyncSession]:
        async with case.db() as db:
            yield db

    monkeypatch.setattr(conversation_stream_service, "async_session", case.db)
    app = create_app()
    app.dependency_overrides[get_db] = isolated_db
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={
            settings.cookie_name_access: create_access_token(case.user_id),
            ACTIVE_ORG_COOKIE: str(unit_context(case.user_id).org_id),
        },
    ) as client:
        yield client


async def _assert_native_and_public_access(
    case: OldRun,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = unit_context(case.user_id)
    async with (
        AsyncPostgresSaver.from_conn_string(case.dsn) as saver,
        case.db() as db,
        _reader_client(case, monkeypatch) as client,
    ):
        monkeypatch.setattr(checkpointer, "_checkpointer", saver)
        ident = case.issued["interruptId"]
        namespace = tuple(case.issued["namespace"])
        root_id = case.issued["rootCheckpointId"]
        conversation = await db.get(Conversation, case.conversation_id)
        run = await db.get(ConversationRun, case.run_id)
        assert conversation is not None and run is not None
        root = await saver.aget_tuple(
            {"configurable": {"thread_id": str(case.conversation_id), "checkpoint_ns": ""}}
        )
        assert root is not None and root.checkpoint["id"] == root_id and root.parent_config
        previous = root.parent_config.get("configurable", {}).get("checkpoint_id")
        assert isinstance(previous, str) and previous != root_id
        stale = await read_proven_interrupts(db, conversation, scope, run, checkpoint_id=previous)
        assert stale.checkpoint_id is None and not stale.interrupts and not stale.actionable

        proven = await read_proven_interrupts(db, conversation, scope, run, checkpoint_id=root_id)
        assert proven.checkpoint_id == root_id and proven.actionable
        assert proven.legacy_child_ids == {ident}
        assert proven.legacy_namespaces == {ident: namespace}
        assert [slot["id"] for slot in proven.interrupts] == [ident]
        assert tuple(proven.interrupts[0]["ns"])[: len(namespace)] == namespace
        assert proven.legacy_bindings[ident].tool_call_id == case.issued["toolCallId"]

        snapshot, frames, cursor = await _read_public_views(client, case)
        assert snapshot["selectedPhysicalRunId"] == snapshot["runId"] == str(case.run_id)
        assert snapshot["physicalInterruptRunId"] == snapshot["interruptRunId"] == str(case.run_id)
        [card] = snapshot["interrupts"]
        [child] = snapshot["subagents"]
        assert card["id"] == ident and card["subagentRunId"] == child["subagentRunId"]
        assert child["legacyInterruptAccess"] and child["interruptIds"] == [ident]
        assert child["runId"] == child["physicalRunId"] == str(case.run_id)
        issuer = next(row for row in snapshot["messages"] if row["id"] == child["parentMessageId"])
        assert child["parentToolCallId"] in {call["id"] for call in issuer["toolCalls"]}
        assert case.issued["toolCallId"] not in json.dumps([snapshot, frames])
        assert frames[-1]["type"] == "RUN_FINISHED" and ident in json.dumps(frames)

        async with case.db.begin() as writer:
            await writer.execute(
                delete(AgentSubAgentLink).where(
                    AgentSubAgentLink.parent_agent_id == case.agent_id,
                    AgentSubAgentLink.sub_agent_id == case.child_id,
                )
            )
        denied = await read_proven_interrupts(db, conversation, scope, run, checkpoint_id=root_id)
        assert not denied.actionable
        assert not denied.legacy_child_ids and not denied.legacy_namespaces
        assert not denied.interrupts and not denied.expires_at

        after, closed, _ = await _read_public_views(client, case)
        assert [message["role"] for message in after["messages"]] == ["user"]
        assert after["subagents"] == after["interrupts"] == []
        assert after["interruptRunId"] is None and after["physicalInterruptRunId"] is None
        assert [frame["type"] for frame in closed] == ["RUN_STARTED", "RUN_ERROR"]
        serialized = json.dumps([after, closed])
        for private_value in (
            ident,
            child["subagentRunId"],
            child["parentToolCallId"],
            case.issued["toolCallId"],
            case.child_name,
            "record_review",
            "Review fixture.",
        ):
            assert private_value not in serialized

        path = f"/api/conversations/{case.conversation_id}/ag-ui"
        params = {"runId": str(case.run_id)}
        page = await client.get(f"{path}/events", params={**params, "after": cursor})
        stream = await client.get(
            f"{path}/stream", params=params, headers={"Last-Event-ID": cursor}
        )
        for response in (page, stream):
            assert response.status_code == 409 and "AG_UI_SNAPSHOT_REQUIRED" in response.text
            assert ident not in response.text

        same_root = await saver.aget_tuple(
            {"configurable": {"thread_id": str(case.conversation_id), "checkpoint_ns": ""}}
        )
        assert same_root is not None and same_root.config == root.config
        assert same_root.pending_writes == root.pending_writes


@pytest.mark.asyncio
async def test_archived_child_capability_tracks_current_root_and_delegation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _archived_run() as case:
        await _assert_native_and_public_access(case, monkeypatch)


@pytest.mark.asyncio
@pytest.mark.parametrize("old_run", [("retained", "recorded")], indirect=True)
async def test_recorded_legacy_child_loses_access_before_journal_replay(
    old_run: OldRun,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _assert_native_and_public_access(old_run, monkeypatch)
