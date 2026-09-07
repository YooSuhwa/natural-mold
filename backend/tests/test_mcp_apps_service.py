from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.prebuilt import ToolNode
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import mcp_app_projection
from app.agent_runtime.langgraph_protocol_adapter import adapt_stream_mode_chunk
from app.agent_runtime.mcp_app_projection import attach_verified_mcp_apps
from app.mcp.apps import normalize_tool_ui_meta, tool_allows_visibility
from app.mcp.domain import McpAppRuntimeContext
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.model import Model
from app.models.user import User
from app.routers.conversation_agent_protocol_state import (
    _checkpoint_state_response,
    _snapshot_state_response,
)
from app.routers.conversation_agent_protocol_state_snapshot import load_thread_state_snapshot
from app.services import mcp_apps_service
from app.services.mcp_apps_binding_service import record_runtime_binding
from app.services.mcp_apps_service import BindingDraft, McpAppsError
from app.services.thread_branch_service import _CheckpointSlim
from tests.conftest import TEST_USER_ID, TestSession


@dataclass(frozen=True, slots=True)
class SeededMcpApp:
    agent: Agent
    conversation: Conversation
    run: ConversationRun
    server: McpServer
    origin: McpTool
    sibling: McpTool
    binding_id: uuid.UUID


class _McpAppStateCheckpointer:
    def __init__(self, message: ToolMessage) -> None:
        self._message = message

    async def alist(self, _config: Any) -> AsyncIterator[Any]:
        yield SimpleNamespace(
            config={"configurable": {"checkpoint_id": "ck-mcp-app"}},
            parent_config=None,
            checkpoint={"channel_values": {"messages": [self._message]}},
        )

    async def aget_tuple(self, _config: Any) -> Any:
        return SimpleNamespace(
            config={"configurable": {"checkpoint_id": "ck-mcp-app"}},
            checkpoint={"channel_values": {"messages": [self._message]}},
            pending_writes=[],
        )


def test_malformed_or_empty_visibility_never_expands_access() -> None:
    assert (
        normalize_tool_ui_meta(
            {"ui": {"resourceUri": "ui://weather/dashboard", "visibility": ["admin"]}}
        )
        is None
    )
    empty = normalize_tool_ui_meta(
        {"ui": {"resourceUri": "ui://weather/dashboard", "visibility": []}}
    )
    assert empty == {"ui": {"resourceUri": "ui://weather/dashboard", "visibility": []}}
    assert tool_allows_visibility(empty, "model") is False
    assert tool_allows_visibility(empty, "app") is False


async def _seed(db: AsyncSession) -> SeededMcpApp:
    user = User(id=TEST_USER_ID, email="mcp-app@test.local", name="MCP App User")
    model = Model(provider="openai", model_name="gpt-4o-mini", display_name="GPT")
    db.add_all([user, model])
    await db.flush()
    agent = Agent(
        user_id=user.id,
        name="MCP App Agent",
        system_prompt="Helpful",
        model_id=model.id,
    )
    db.add(agent)
    await db.flush()
    conversation = Conversation(agent_id=agent.id, title="MCP App")
    db.add(conversation)
    await db.flush()
    run = ConversationRun(
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=user.id,
        source="chat",
        status="completed",
        is_active=False,
    )
    server = McpServer(
        user_id=user.id,
        name="Weather",
        transport="streamable_http",
        url="https://mcp.weather.test/rpc",
        headers={"Authorization": "Bearer server-secret"},
        status="connected",
    )
    db.add_all([run, server])
    await db.flush()
    origin = McpTool(
        server_id=server.id,
        name="weather",
        input_schema={"type": "object"},
        metadata_json={"ui": {"resourceUri": "ui://weather/dashboard"}},
        enabled=True,
    )
    sibling = McpTool(
        server_id=server.id,
        name="refresh_weather",
        input_schema={"type": "object"},
        metadata_json={"ui": {"visibility": ["app"]}},
        enabled=True,
    )
    db.add_all([origin, sibling])
    await db.flush()
    db.add_all(
        [
            AgentMcpToolLink(agent_id=agent.id, mcp_tool_id=origin.id),
            AgentMcpToolLink(agent_id=agent.id, mcp_tool_id=sibling.id),
        ]
    )
    await db.flush()
    binding_id = await mcp_apps_service.mint_invocation_binding(
        db,
        BindingDraft(
            run_id=run.id,
            conversation_id=conversation.id,
            user_id=user.id,
            agent_id=agent.id,
            credential_subject_user_id=user.id,
            mcp_server_id=server.id,
            mcp_tool_id=origin.id,
            tool_call_id="tool-call-1",
            remote_tool_name=origin.name,
            resource_uri="ui://weather/dashboard",
            tool_meta={"ui": {"resourceUri": "ui://weather/dashboard"}},
            result_meta={"viewUUID": "view-1", "access_token": "drop"},
            structured_content={"temperature": 23},
        ),
    )
    await db.commit()
    return SeededMcpApp(agent, conversation, run, server, origin, sibling, binding_id)


def _use_test_projection_database(monkeypatch: pytest.MonkeyPatch) -> None:
    load_verified_artifact = mcp_app_projection.load_verified_artifact

    async def _load_verified_artifact(**kwargs: Any) -> dict[str, Any] | None:
        return await load_verified_artifact(**kwargs, session_factory=TestSession)

    monkeypatch.setattr(
        mcp_app_projection,
        "load_verified_artifact",
        _load_verified_artifact,
    )


@pytest.mark.asyncio
async def test_read_bound_resource_uses_server_connection_and_allowlists_meta(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = await _seed(db)
    captured: dict[str, Any] = {}

    async def _read(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "success": True,
            "contents": [
                {
                    "uri": "ui://weather/dashboard",
                    "mimeType": "text/html;profile=mcp-app",
                    "text": "<!doctype html><title>Weather</title>",
                    "_meta": {
                        "ui": {
                            "csp": {"connectDomains": ["https://api.weather.test"]},
                            "prefersBorder": True,
                            "transport_headers": {"Authorization": "leak"},
                        },
                        "credential": "leak",
                    },
                }
            ],
        }

    monkeypatch.setattr(mcp_apps_service, "read_mcp_resource_once", _read)
    context = await mcp_apps_service.authorize_binding(
        db,
        conversation_id=seeded.conversation.id,
        run_id=seeded.run.id,
        tool_call_id="tool-call-1",
        user_id=TEST_USER_ID,
    )

    result = await mcp_apps_service.read_bound_resource(db, context)

    assert captured["url"] == "https://mcp.weather.test/rpc"
    assert captured["headers"] == {"Authorization": "Bearer server-secret"}
    assert captured["resource_uri"] == "ui://weather/dashboard"
    assert result == {
        "uri": "ui://weather/dashboard",
        "mimeType": "text/html;profile=mcp-app",
        "html": "<!doctype html><title>Weather</title>",
        "meta": {
            "csp": {"connectDomains": ["https://api.weather.test"]},
            "prefersBorder": True,
        },
    }
    assert "server-secret" not in str(result)
    assert "leak" not in str(result)


@pytest.mark.asyncio
async def test_call_bound_sibling_requires_current_same_server_agent_link(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = await _seed(db)
    captured: dict[str, Any] = {}

    async def _call(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "success": True,
            "content": [{"type": "text", "text": "server-secret refreshed"}],
            "structured_content": {"ok": True, "echo": "Bearer server-secret"},
            "meta": {"viewUUID": "view-2", "authorization": "drop"},
        }

    monkeypatch.setattr(mcp_apps_service, "call_mcp_tool_once", _call)
    context = await mcp_apps_service.authorize_binding(
        db,
        conversation_id=seeded.conversation.id,
        run_id=seeded.run.id,
        tool_call_id="tool-call-1",
        user_id=TEST_USER_ID,
    )

    result = await mcp_apps_service.call_bound_tool(
        db,
        context,
        tool_name="refresh_weather",
        arguments={"city": "Seoul"},
    )

    assert captured["tool_name"] == "refresh_weather"
    assert result["structuredContent"] == {"ok": True, "echo": "<redacted>"}
    assert result["_meta"] == {"viewUUID": "view-2"}
    assert "authorization" not in str(result).lower()
    assert "server-secret" not in str(result)


@pytest.mark.asyncio
async def test_authorize_binding_hides_cross_user_and_rejects_stale_or_revoked(
    db: AsyncSession,
) -> None:
    seeded = await _seed(db)

    with pytest.raises(McpAppsError) as cross_user:
        await mcp_apps_service.authorize_binding(
            db,
            conversation_id=seeded.conversation.id,
            run_id=seeded.run.id,
            tool_call_id="tool-call-1",
            user_id=uuid.uuid4(),
        )
    assert cross_user.value.status_code == 404

    seeded.origin.metadata_json = {"ui": {"resourceUri": "ui://weather/replaced"}}
    await db.flush()
    with pytest.raises(McpAppsError) as stale:
        await mcp_apps_service.authorize_binding(
            db,
            conversation_id=seeded.conversation.id,
            run_id=seeded.run.id,
            tool_call_id="tool-call-1",
            user_id=TEST_USER_ID,
        )
    assert stale.value.code == "mcp_app_context_stale"

    seeded.origin.metadata_json = {"ui": {"resourceUri": "ui://weather/dashboard"}}
    seeded.origin.enabled = False
    await db.flush()
    with pytest.raises(McpAppsError) as revoked:
        await mcp_apps_service.authorize_binding(
            db,
            conversation_id=seeded.conversation.id,
            run_id=seeded.run.id,
            tool_call_id="tool-call-1",
            user_id=TEST_USER_ID,
        )
    assert revoked.value.status_code == 403


@pytest.mark.asyncio
async def test_call_bound_tool_rejects_cross_server_name_and_oversized_arguments(
    db: AsyncSession,
) -> None:
    seeded = await _seed(db)
    other_server = McpServer(
        user_id=TEST_USER_ID,
        name="Other",
        transport="streamable_http",
        url="https://other.test/rpc",
        status="connected",
    )
    db.add(other_server)
    await db.flush()
    forged = McpTool(
        server_id=other_server.id,
        name="admin_action",
        input_schema={},
        metadata_json={"ui": {"visibility": ["app"]}},
        enabled=True,
    )
    db.add(forged)
    await db.flush()
    db.add(AgentMcpToolLink(agent_id=seeded.agent.id, mcp_tool_id=forged.id))
    await db.flush()
    context = await mcp_apps_service.authorize_binding(
        db,
        conversation_id=seeded.conversation.id,
        run_id=seeded.run.id,
        tool_call_id="tool-call-1",
        user_id=TEST_USER_ID,
    )

    with pytest.raises(McpAppsError) as cross_server:
        await mcp_apps_service.call_bound_tool(db, context, tool_name="admin_action", arguments={})
    assert cross_server.value.status_code == 403

    with pytest.raises(McpAppsError) as oversized:
        await mcp_apps_service.call_bound_tool(
            db,
            context,
            tool_name="refresh_weather",
            arguments={"payload": "x" * (65 * 1024)},
        )
    assert oversized.value.status_code == 413


@pytest.mark.asyncio
async def test_read_bound_resource_rejects_malformed_mime_without_returning_html(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = await _seed(db)

    async def _read(**_kwargs: Any) -> dict[str, Any]:
        return {
            "success": True,
            "contents": [
                {
                    "uri": "ui://weather/dashboard",
                    "mimeType": "text/html",
                    "text": "<script>bad()</script>",
                }
            ],
        }

    monkeypatch.setattr(mcp_apps_service, "read_mcp_resource_once", _read)
    context = await mcp_apps_service.authorize_binding(
        db,
        conversation_id=seeded.conversation.id,
        run_id=seeded.run.id,
        tool_call_id="tool-call-1",
        user_id=TEST_USER_ID,
    )

    with pytest.raises(McpAppsError) as malformed:
        await mcp_apps_service.read_bound_resource(db, context)

    assert malformed.value.code == "mcp_app_resource_invalid"


@pytest.mark.asyncio
async def test_read_bound_resource_rejects_server_credential_echo(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = await _seed(db)

    async def _read(**_kwargs: Any) -> dict[str, Any]:
        return {
            "success": True,
            "contents": [
                {
                    "uri": "ui://weather/dashboard",
                    "mimeType": "text/html;profile=mcp-app",
                    "text": "<script>window.key='server-secret'</script>",
                }
            ],
        }

    monkeypatch.setattr(mcp_apps_service, "read_mcp_resource_once", _read)
    context = await mcp_apps_service.authorize_binding(
        db,
        conversation_id=seeded.conversation.id,
        run_id=seeded.run.id,
        tool_call_id="tool-call-1",
        user_id=TEST_USER_ID,
    )

    with pytest.raises(McpAppsError) as sensitive:
        await mcp_apps_service.read_bound_resource(db, context)

    assert sensitive.value.code == "mcp_app_resource_sensitive"


@pytest.mark.asyncio
async def test_call_bound_tool_rejects_oversized_server_result(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = await _seed(db)

    async def _call(**_kwargs: Any) -> dict[str, Any]:
        return {"success": True, "content": [{"type": "text", "text": "x" * 140_000}]}

    monkeypatch.setattr(mcp_apps_service, "call_mcp_tool_once", _call)
    context = await mcp_apps_service.authorize_binding(
        db,
        conversation_id=seeded.conversation.id,
        run_id=seeded.run.id,
        tool_call_id="tool-call-1",
        user_id=TEST_USER_ID,
    )

    with pytest.raises(McpAppsError) as oversized:
        await mcp_apps_service.call_bound_tool(
            db, context, tool_name="refresh_weather", arguments={}
        )

    assert oversized.value.code == "mcp_app_result_too_large"


@pytest.mark.asyncio
async def test_call_bound_tool_does_not_expose_transport_error_secret(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = await _seed(db)

    async def _call(**_kwargs: Any) -> dict[str, Any]:
        return {"success": False, "error": "Authorization Bearer server-secret failed"}

    monkeypatch.setattr(mcp_apps_service, "call_mcp_tool_once", _call)
    context = await mcp_apps_service.authorize_binding(
        db,
        conversation_id=seeded.conversation.id,
        run_id=seeded.run.id,
        tool_call_id="tool-call-1",
        user_id=TEST_USER_ID,
    )

    with pytest.raises(McpAppsError) as failed:
        await mcp_apps_service.call_bound_tool(
            db, context, tool_name="refresh_weather", arguments={}
        )

    assert failed.value.message == "MCP App tool call failed"
    assert "server-secret" not in failed.value.message


@pytest.mark.asyncio
async def test_runtime_binding_callback_persists_only_trusted_runtime_context(
    db: AsyncSession,
) -> None:
    seeded = await _seed(db)
    context = McpAppRuntimeContext(
        conversation_id=seeded.conversation.id,
        user_id=TEST_USER_ID,
        agent_id=seeded.agent.id,
        credential_subject_user_id=TEST_USER_ID,
        mcp_server_id=seeded.server.id,
        mcp_tool_id=seeded.origin.id,
        remote_tool_name=seeded.origin.name,
        resource_uri="ui://weather/dashboard",
        tool_meta={"ui": {"resourceUri": "ui://weather/dashboard"}},
    )

    binding_id = await record_runtime_binding(
        context,
        str(seeded.run.id),
        "actual-runtime-tool-call",
        {"viewUUID": "view-2", "access_token": "drop"},
        {"temperature": 24},
        session_factory=TestSession,
    )

    assert uuid.UUID(binding_id or "")
    authorized = await mcp_apps_service.authorize_binding(
        db,
        conversation_id=seeded.conversation.id,
        run_id=seeded.run.id,
        tool_call_id="actual-runtime-tool-call",
        user_id=TEST_USER_ID,
    )
    assert authorized.binding.result_meta == {"viewUUID": "view-2"}
    assert authorized.binding.structured_content == {"temperature": 24}


@pytest.mark.asyncio
async def test_projection_requires_persisted_run_tool_call_binding_and_supports_resume(
    db: AsyncSession,
) -> None:
    seeded = await _seed(db)
    claimed = {
        "version": 1,
        "binding_id": str(seeded.binding_id),
        "run_id": str(seeded.run.id),
        "resource_uri": "ui://forged/resource",
        "tool_meta": {"ui": {"resourceUri": "ui://forged/resource"}},
        "result_meta": {"forged": True},
    }
    message = ToolMessage(
        content="weather",
        tool_call_id="tool-call-1",
        artifact={"mcp_app": claimed, "structured_content": {"forged": True}},
    )
    event = adapt_stream_mode_chunk(
        ("messages", (message, {"langgraph_node": "tools"})),
        run_id=str(seeded.run.id),
        thread_id=str(seeded.conversation.id),
        seq=1,
    )

    assert "artifact" not in event["data"]
    verified = await attach_verified_mcp_apps(
        event,
        message,
        expected_conversation_id=str(seeded.conversation.id),
        expected_run_id=str(seeded.run.id),
        session_factory=TestSession,
    )
    assert verified["data"]["artifact"] == {
        "mcp_app": {
            "version": 1,
            "binding_id": str(seeded.binding_id),
            "run_id": str(seeded.run.id),
            "resource_uri": "ui://weather/dashboard",
            "tool_meta": {"ui": {"resourceUri": "ui://weather/dashboard"}},
            "result_meta": {"viewUUID": "view-1"},
        },
        "structured_content": {"temperature": 23},
    }

    forged = await attach_verified_mcp_apps(
        event,
        ToolMessage(
            content="weather",
            tool_call_id="different-call",
            artifact={"mcp_app": claimed, "structured_content": {"untrusted": True}},
        ),
        expected_conversation_id=str(seeded.conversation.id),
        expected_run_id=str(seeded.run.id),
        session_factory=TestSession,
    )
    assert "artifact" not in forged["data"]

    wrong_run_claim = {**claimed, "run_id": str(uuid.uuid4())}
    mismatched_run = await attach_verified_mcp_apps(
        event,
        ToolMessage(
            content="weather",
            tool_call_id="tool-call-1",
            artifact={"mcp_app": wrong_run_claim},
        ),
        expected_conversation_id=str(seeded.conversation.id),
        expected_run_id=str(seeded.run.id),
        session_factory=TestSession,
    )
    assert "artifact" not in mismatched_run["data"]

    untrusted_values = {
        "messages": [
            {
                "type": "tool",
                "tool_call_id": "tool-call-1",
                "artifact": {
                    "mcp_app": wrong_run_claim,
                    "structured_content": {"untrusted": "must-not-survive"},
                },
            }
        ]
    }
    untrusted_event = adapt_stream_mode_chunk(
        ("values", untrusted_values),
        run_id=str(seeded.run.id),
        thread_id=str(seeded.conversation.id),
        seq=2,
    )
    scrubbed = await attach_verified_mcp_apps(
        untrusted_event,
        untrusted_values,
        expected_conversation_id=str(seeded.conversation.id),
        expected_run_id=str(seeded.run.id),
        session_factory=TestSession,
    )
    assert "artifact" not in scrubbed["data"]["messages"][0]

    resumed_raw = {
        "messages": [
            {
                "tool_call_id": "tool-call-1",
                "artifact": {"mcp_app": claimed},
            }
        ]
    }
    resumed_event = adapt_stream_mode_chunk(
        ("values", resumed_raw),
        run_id=str(seeded.run.id),
        thread_id=str(seeded.conversation.id),
        seq=3,
    )
    resumed = await attach_verified_mcp_apps(
        resumed_event,
        resumed_raw,
        expected_conversation_id=str(seeded.conversation.id),
        expected_run_id=str(seeded.run.id),
        session_factory=TestSession,
    )
    assert resumed["data"]["messages"][0]["artifact"] == verified["data"]["artifact"]


@pytest.mark.asyncio
async def test_actual_toolnode_binding_is_persisted_and_rehydrated_on_resume(
    db: AsyncSession,
) -> None:
    from langchain.tools import ToolRuntime
    from langgraph._internal._constants import CONFIG_KEY_RUNTIME

    from app.agent_runtime.mcp_cache import _MCP_RESULT_ENVELOPE, MCPToolWithRetry

    seeded = await _seed(db)
    context = McpAppRuntimeContext(
        conversation_id=seeded.conversation.id,
        user_id=TEST_USER_ID,
        agent_id=seeded.agent.id,
        credential_subject_user_id=TEST_USER_ID,
        mcp_server_id=seeded.server.id,
        mcp_tool_id=seeded.origin.id,
        remote_tool_name=seeded.origin.name,
        resource_uri="ui://weather/dashboard",
        tool_meta={"ui": {"resourceUri": "ui://weather/dashboard"}},
    )

    async def _remote_call(**_kwargs: Any) -> tuple[str, dict[str, Any]]:
        return (
            "sunny",
            {
                "structured_content": {
                    _MCP_RESULT_ENVELOPE: {
                        "structured_content": {"temperature": 25},
                        "result_meta": {"viewUUID": "actual-view"},
                    }
                }
            },
        )

    async def _persist(
        received_context: McpAppRuntimeContext,
        run_id: str,
        tool_call_id: str,
        result_meta: dict[str, Any] | None,
        structured_content: dict[str, Any] | None,
    ) -> str | None:
        return await record_runtime_binding(
            received_context,
            run_id,
            tool_call_id,
            result_meta,
            structured_content,
            session_factory=TestSession,
        )

    original = StructuredTool.from_function(
        coroutine=_remote_call,
        name="weather",
        description="weather",
        response_format="content_and_artifact",
    )
    wrapped = MCPToolWithRetry(
        original,
        mcp_app_context=context,
        binding_recorder=_persist,
    )
    node = ToolNode([wrapped])
    run_id = str(seeded.run.id)
    config: RunnableConfig = {"configurable": {"moldy_run_id": run_id}}
    configurable = cast(dict[str, Any], config["configurable"])
    configurable[CONFIG_KEY_RUNTIME] = ToolRuntime(
        state={},
        context=None,
        config=config,
        stream_writer=lambda _chunk: None,
        tool_call_id=None,
        store=None,
        tools=[wrapped],
    )

    state = await node.ainvoke(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "weather",
                            "args": {},
                            "id": "persisted-actual-call",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        },
        config=config,
    )
    message = state["messages"][0]
    assert isinstance(message, ToolMessage)
    assert message.tool_call_id == "persisted-actual-call"

    event = adapt_stream_mode_chunk(
        ("values", {"messages": [message]}),
        run_id=run_id,
        thread_id=str(seeded.conversation.id),
        seq=1,
    )
    assert "artifact" not in event["data"]["messages"][0]
    rehydrated = await attach_verified_mcp_apps(
        event,
        {"messages": [message]},
        expected_conversation_id=str(seeded.conversation.id),
        expected_run_id=run_id,
        session_factory=TestSession,
    )
    artifact = rehydrated["data"]["messages"][0]["artifact"]
    assert artifact["structured_content"] == {"temperature": 25}
    assert artifact["mcp_app"]["run_id"] == run_id
    assert artifact["mcp_app"]["resource_uri"] == "ui://weather/dashboard"

    resumed = await attach_verified_mcp_apps(
        event,
        {
            "messages": [
                {
                    "tool_call_id": "persisted-actual-call",
                    "artifact": {"mcp_app": artifact["mcp_app"]},
                }
            ]
        },
        expected_conversation_id=str(seeded.conversation.id),
        expected_run_id=run_id,
        session_factory=TestSession,
    )
    assert resumed["data"]["messages"][0]["artifact"] == artifact


@pytest.mark.asyncio
async def test_history_and_update_state_only_project_canonical_bound_app_artifacts(
    db: AsyncSession,
) -> None:
    seeded = await _seed(db)
    canonical_claim = {
        "version": 1,
        "binding_id": str(seeded.binding_id),
        "run_id": str(seeded.run.id),
        "resource_uri": "ui://forged/resource",
        "tool_meta": {"ui": {"resourceUri": "ui://forged/resource"}},
    }
    bound_message = ToolMessage(
        content="weather",
        tool_call_id="tool-call-1",
        artifact={
            "mcp_app": canonical_claim,
            "structured_content": {"forged": "must-not-survive"},
        },
    )
    history = await _checkpoint_state_response(
        seeded.conversation,
        checkpoint=_CheckpointSlim("checkpoint-1", None, [bound_message]),
        checkpoint_by_message_id={},
        session_factory=TestSession,
    )
    history_values = cast(dict[str, Any], history["values"])
    history_artifact = history_values["messages"][0]["artifact"]
    assert history_artifact["structured_content"] == {"temperature": 23}
    assert history_artifact["mcp_app"]["resource_uri"] == "ui://weather/dashboard"

    wrong_run_claim = {**canonical_claim, "run_id": str(uuid.uuid4())}
    forged_values = {
        "messages": [
            {
                "type": "tool",
                "tool_call_id": "tool-call-1",
                "content": "weather",
                "artifact": {
                    "mcp_app": wrong_run_claim,
                    "structured_content": {"untrusted": "must-not-survive"},
                },
            }
        ]
    }
    forged_update = await _snapshot_state_response(
        seeded.conversation,
        SimpleNamespace(values=forged_values, config={}, next=(), tasks=()),
        session_factory=TestSession,
    )
    forged_update_values = cast(dict[str, Any], forged_update["values"])
    assert "artifact" not in forged_update_values["messages"][0]

    valid_update = await _snapshot_state_response(
        seeded.conversation,
        SimpleNamespace(
            values={
                "messages": [
                    {
                        "type": "tool",
                        "tool_call_id": "tool-call-1",
                        "content": "weather",
                        "artifact": {"mcp_app": canonical_claim},
                    }
                ]
            },
            config={},
            next=(),
            tasks=(),
        ),
        session_factory=TestSession,
    )
    valid_update_values = cast(dict[str, Any], valid_update["values"])
    assert valid_update_values["messages"][0]["artifact"] == history_artifact


@pytest.mark.asyncio
async def test_current_state_projects_only_canonical_bound_mcp_app_artifact(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = await _seed(db)
    raw_message = ToolMessage(
        content="weather",
        tool_call_id="tool-call-1",
        artifact={
            "mcp_app": {
                "version": 1,
                "binding_id": str(seeded.binding_id),
                "run_id": str(seeded.run.id),
                "resource_uri": "ui://forged/resource",
                "tool_meta": {"ui": {"resourceUri": "ui://forged/resource"}},
            },
            "structured_content": {"forged": "must-not-survive"},
        },
    )
    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol_state_snapshot.get_checkpointer",
        lambda: _McpAppStateCheckpointer(raw_message),
    )
    _use_test_projection_database(monkeypatch)

    snapshot = await load_thread_state_snapshot(seeded.conversation)

    artifact = snapshot.values["messages"][0]["artifact"]
    assert artifact["structured_content"] == {"temperature": 23}
    assert artifact["mcp_app"]["resource_uri"] == "ui://weather/dashboard"
    assert artifact["mcp_app"]["result_meta"] == {"viewUUID": "view-1"}


@pytest.mark.asyncio
async def test_current_state_strips_wrong_run_mcp_app_artifact(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = await _seed(db)
    raw_message = ToolMessage(
        content="weather",
        tool_call_id="tool-call-1",
        artifact={
            "mcp_app": {
                "version": 1,
                "binding_id": str(seeded.binding_id),
                "run_id": str(uuid.uuid4()),
                "resource_uri": "ui://weather/dashboard",
                "tool_meta": {"ui": {"resourceUri": "ui://weather/dashboard"}},
            },
            "structured_content": {"untrusted": "must-not-survive"},
        },
    )
    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol_state_snapshot.get_checkpointer",
        lambda: _McpAppStateCheckpointer(raw_message),
    )
    _use_test_projection_database(monkeypatch)

    snapshot = await load_thread_state_snapshot(seeded.conversation)

    assert "artifact" not in snapshot.values["messages"][0]
