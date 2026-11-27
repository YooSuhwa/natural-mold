"""A persisted widget must disappear from every journal read after a disconnect."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Message, Receive, Scope, Send

from app.agent_runtime.langgraph_lifecycle_events import lifecycle_protocol_event
from app.agent_runtime.protocol_events import stored_protocol_event
from app.credentials import service as credential_service
from app.credentials.creation import CredentialCreation
from app.models.mcp_server import McpServer
from app.models.resource_credential_binding import ResourceCredentialBinding
from app.services import trace_storage
from tests.test_ag_ui_widget_access import widget as widget

if TYPE_CHECKING:
    from tests.test_ag_ui_widget_access import Widget

pytest_plugins = ["tests.authz.private_scope_fixture", "tests.authz.private_mcp_apps_fixture"]


@pytest.fixture
async def connection(db: AsyncSession, widget: Widget) -> ResourceCredentialBinding:
    ctx = widget.context
    server = await db.get(McpServer, widget.original.mcp_server_id)
    assert server is not None
    credential = await credential_service.create(
        db,
        CredentialCreation(definition_key="http_bearer", name="Widget", data={"token": "test"}),
        ctx,
    )
    server.credential_mode, server.credential_definition_key = "per_user", "http_bearer"
    row = ResourceCredentialBinding(
        org_id=ctx.org_id,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        resource_type="mcp_server",
        resource_id=server.id,
        credential_id=credential.id,
    )
    db.add(row)
    run_id, thread_id = str(widget.run.id), str(widget.conversation.id)
    tool_call_id, name = widget.original.tool_call_id, widget.original.remote_tool_name
    request = stored_protocol_event(
        run_id=run_id,
        thread_id=thread_id,
        seq=1,
        method="messages",
        data={
            "id": "request",
            "type": "ai",
            "tool_calls": [{"id": tool_call_id, "name": name, "args": {}}],
        },
    )
    result = stored_protocol_event(
        run_id=run_id,
        thread_id=thread_id,
        seq=2,
        method="messages",
        data={
            "id": "answer",
            "type": "tool",
            "tool_call_id": tool_call_id,
            "name": name,
            "content": "Processed",
            "artifact": {
                "mcp_app": {
                    "version": 1,
                    "run_id": run_id,
                    "binding_id": str(widget.minted),
                    "resource_uri": widget.original.resource_uri,
                }
            },
        },
    )
    terminal = lifecycle_protocol_event(
        run_id=run_id, thread_id=thread_id, seq=3, event="completed"
    )
    await trace_storage.append_events(
        db,
        conversation_id=widget.conversation.id,
        assistant_msg_id=run_id,
        events_chunk=[dict(request), dict(result), dict(terminal)],
    )
    await trace_storage.finalize_turn(db, assistant_msg_id=run_id, status="completed")
    await db.commit()
    return row


async def test_disconnect_while_delivering_invalidates_cursor_and_hides_widget(
    raw_test_app: FastAPI,
    raw_client: AsyncClient,
    db: AsyncSession,
    widget: Widget,
    connection: ResourceCredentialBinding,
) -> None:
    url = f"/api/conversations/{widget.conversation.id}/ag-ui"
    params = {"runId": str(widget.run.id)}
    first = await raw_client.get(f"{url}/events", params=params)
    assert first.status_code == 200, first.text
    (card,) = [
        frame
        for frame in first.json()["frames"]
        if frame["event"].get("activityType") == "moldy.mcp_app"
    ]
    assert card["event"]["content"]["binding_id"] == str(widget.minted)
    assert card["event"]["content"]["tool_call_id"] == widget.original.tool_call_id

    async def revoke_during_send(scope: Scope, receive: Receive, send: Send) -> None:
        async def send_to_tab(message: Message) -> None:
            await send(message)
            # The whole batch was projected before RUN_STARTED. Withdraw access
            # while this tab is backpressured, before the next frame is delivered.
            if b'"type":"RUN_STARTED"' in message.get("body", b""):
                await db.delete(connection)
                await db.commit()

        await raw_test_app(scope, receive, send_to_tab)

    async with AsyncClient(
        base_url="http://test",
        transport=ASGITransport(app=revoke_during_send),
        cookies=raw_client.cookies,
        headers={"Accept-Encoding": "identity"},
    ) as tab:
        held = await tab.get(f"{url}/stream", params=params)
    assert held.status_code == 200
    sent = [json.loads(line[6:]) for line in held.text.splitlines() if line.startswith("data: ")]
    assert sent[0]["type"] == "RUN_STARTED"
    assert (sent[-1]["type"], sent[-1]["code"]) == ("RUN_ERROR", "AG_UI_SNAPSHOT_REQUIRED")
    assert str(widget.minted) not in held.text

    stale = await raw_client.get(f"{url}/events", params={**params, "after": card["id"]})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "AG_UI_SNAPSHOT_REQUIRED"
    for path in ("events", "stream"):
        fresh = await raw_client.get(f"{url}/{path}", params=params)
        assert fresh.status_code == 200, fresh.text
        assert "RUN_FINISHED" in fresh.text
        assert "moldy.mcp_app" not in fresh.text
        assert str(widget.minted) not in fresh.text
