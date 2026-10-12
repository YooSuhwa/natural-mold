from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.agent import Agent
from app.models.conversation_history import (
    ConversationHistoryCheckpoint,
    ConversationHistoryEvent,
)
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.message_event import MessageEvent
from app.models.model import Model
from app.services.conversation_history_snapshot import materialize_conversation_history
from tests.conftest import TestSession
from tests.conversation_history_snapshot_fixture import (
    SECRET,
    authorize_snapshot_agent,
    collect_fixture_secret,
    collect_no_secrets,
    materialization_request,
    namespaced_snapshot_case,
    snapshot_case,
)


@pytest.mark.asyncio
async def test_purge_snapshot_retains_branched_history_without_live_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = await snapshot_case()
    async with TestSession() as db:
        db.add(
            MessageEvent(
                conversation_id=case.conversation.id,
                assistant_msg_id="retained-run",
                events=[
                    {
                        "id": "retained-run-1",
                        "event": "message_end",
                        "data": {"content": f"legacy {SECRET}"},
                    }
                ],
            )
        )
        await db.commit()
        monkeypatch.setattr(
            "app.services.conversation_history_snapshot.authorize_agent_lifecycle",
            authorize_snapshot_agent,
        )
        monkeypatch.setattr(
            "app.services.conversation_history_snapshot.collect_strict_history_secrets",
            collect_fixture_secret,
        )

        manifest = await materialize_conversation_history(materialization_request(db, case))
        await db.commit()
        repeated = await materialize_conversation_history(materialization_request(db, case))
        raw_headers = [
            item
            async for item in case.saver.alist(
                {"configurable": {"thread_id": str(case.conversation.id)}}
            )
        ]
        checkpoints = list(
            await db.scalars(
                select(ConversationHistoryCheckpoint).order_by(
                    ConversationHistoryCheckpoint.checkpoint_id
                )
            )
        )
        events = list(await db.scalars(select(ConversationHistoryEvent)))

    assert (manifest.state, manifest.checkpoint_count, manifest.event_count) == (
        "complete",
        2,
        1,
    )
    assert manifest.checkpoint_count == len(raw_headers)
    assert (repeated.id, repeated.canonical_payload_sha256) == (
        manifest.id,
        manifest.canonical_payload_sha256,
    )
    assert [(row.checkpoint_id, row.parent_checkpoint_id) for row in checkpoints] == [
        ("checkpoint-branch", "checkpoint-root"),
        ("checkpoint-root", None),
    ]
    assert "message-ai" in str(checkpoints[0].messages)
    assert "message-user" in str(checkpoints[1].messages)
    assert SECRET not in str([row.channel_values for row in checkpoints])
    assert SECRET not in str([row.messages for row in checkpoints])
    assert SECRET not in str([row.events for row in events])
    retained = str([(row.channel_values, row.messages, row.trace) for row in checkpoints])
    assert all(
        forbidden not in retained
        for forbidden in ("AgentConfig", "credentials", "transport_authority", "tools")
    )


@pytest.mark.asyncio
async def test_materializer_masks_custom_authorization_header_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = await snapshot_case()
    full_header = f"Static {SECRET}"
    async with TestSession() as db:
        model = Model(
            provider="openai_compatible",
            model_name="history-static-authorization",
            display_name="History static authorization",
            is_default=False,
            is_visible=False,
            source="manual",
        )
        server = McpServer(
            tenant_id=case.conversation.tenant_id,
            org_id=case.conversation.org_id,
            user_id=case.conversation.user_id,
            name="History static authorization",
            transport="stdio",
            headers={"Authorization": full_header},
            status="disabled",
            is_system=False,
        )
        db.add_all([model, server])
        await db.flush()
        agent = Agent(
            id=case.conversation.agent_id,
            tenant_id=case.conversation.tenant_id,
            org_id=case.conversation.org_id,
            user_id=case.conversation.user_id,
            name="Snapshot Agent",
            system_prompt="history",
            model_id=model.id,
            status="active",
        )
        tool = McpTool(
            server_id=server.id,
            name="history_static_authorization",
            input_schema={"type": "object"},
            enabled=True,
        )
        db.add_all([agent, tool])
        await db.flush()
        db.add_all(
            [
                AgentMcpToolLink(agent_id=agent.id, mcp_tool_id=tool.id),
                MessageEvent(
                    conversation_id=case.conversation.id,
                    assistant_msg_id="static-authorization",
                    events=[
                        {
                            "id": "static-authorization-1",
                            "event": "message_end",
                            "data": {"content": f"{full_header} {SECRET}"},
                        }
                    ],
                    external_trace_url=f"https://trace.invalid/{SECRET}",
                ),
            ]
        )
        await db.commit()
        monkeypatch.setattr(
            "app.services.conversation_history_snapshot.authorize_agent_lifecycle",
            authorize_snapshot_agent,
        )

        manifest = await materialize_conversation_history(materialization_request(db, case))
        checkpoints = list(await db.scalars(select(ConversationHistoryCheckpoint)))
        events = list(await db.scalars(select(ConversationHistoryEvent)))

    retained = str(
        [
            (row.channel_values, row.messages, row.trace)
            for row in checkpoints
            if row.manifest_id == manifest.id
        ]
        + [row.events for row in events if row.manifest_id == manifest.id]
    )
    assert SECRET not in retained
    assert full_header not in retained


@pytest.mark.asyncio
async def test_same_checkpoint_id_preserves_each_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = await namespaced_snapshot_case()
    monkeypatch.setattr(
        "app.services.conversation_history_snapshot.authorize_agent_lifecycle",
        authorize_snapshot_agent,
    )
    monkeypatch.setattr(
        "app.services.conversation_history_snapshot.collect_strict_history_secrets",
        collect_no_secrets,
    )
    async with TestSession() as db:
        manifest = await materialize_conversation_history(materialization_request(db, case))
        rows = list(
            await db.scalars(
                select(ConversationHistoryCheckpoint).order_by(
                    ConversationHistoryCheckpoint.checkpoint_namespace
                )
            )
        )
    assert manifest.checkpoint_count == 2
    assert [row.checkpoint_namespace for row in rows] == ["alpha", "beta"]
    assert [row.channel_values["marker"] for row in rows] == [
        "alpha-value",
        "beta-value",
    ]
    assert "alpha-message" in str(rows[0].messages)
    assert "beta-message" in str(rows[1].messages)
    assert rows[0].payload_sha256 != rows[1].payload_sha256
