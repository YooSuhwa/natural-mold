"""Greenfield chat-runtime regression tests.

These tests cover the M5 wiring contract:
- ``chat_service.get_agent_with_tools`` eager-loads everything the runtime
  needs (model, llm_credential, tool credentials, skills) without lazy IO.
- ``chat_service.build_tools_config`` decrypts each tool's credential.
- ``trigger_executor.execute_trigger`` uses the same prefetch path as the
  conversations router (closing the prior prefetch-skew bug).
- ``app.mcp.client.connect_and_list`` interpolates ``${credential.<field>}``
  placeholders via the new credentials interpolation module.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.mcp import client as mcp_client
from app.models.agent import Agent
from app.models.agent_trigger import AgentTrigger
from app.models.model import Model
from app.models.tool import AgentToolLink, Tool
from app.models.user import User
from app.services import chat_service
from tests.conftest import TEST_USER_ID, TestSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_user_and_model(db: AsyncSession) -> Model:
    db.add(User(id=TEST_USER_ID, email="ci@test", name="ci"))
    model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
    db.add(model)
    await db.flush()
    return model


# ---------------------------------------------------------------------------
# Scenario 1: agent + llm_credential + tool credential → tools_config has
# decrypted credential payload.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_tools_config_includes_decrypted_credentials(
    db: AsyncSession,
) -> None:
    model = await _seed_user_and_model(db)

    llm_cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="openai",
        name="agent llm",
        data={"api_key": "sk-llm-secret"},
    )
    tool_cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="naver_search",
        name="naver creds",
        data={"client_id": "nv-id", "client_secret": "nv-secret"},
    )

    agent = Agent(
        user_id=TEST_USER_ID,
        name="Integ Agent",
        system_prompt="hi",
        model_id=model.id,
        llm_credential_id=llm_cred.id,
    )
    db.add(agent)
    await db.flush()

    tool = Tool(
        user_id=TEST_USER_ID,
        name="Naver Blog",
        definition_key="naver_search_blog",
        parameters={"query": "moldy"},
        credential_id=tool_cred.id,
    )
    db.add(tool)
    await db.flush()
    db.add(AgentToolLink(agent_id=agent.id, tool_id=tool.id))
    await db.commit()

    fetched = await chat_service.get_agent_with_tools(db, agent.id, TEST_USER_ID)
    assert fetched is not None
    assert fetched.llm_credential is not None
    assert fetched.llm_credential.id == llm_cred.id
    assert len(fetched.tool_links) == 1
    assert fetched.tool_links[0].tool.credential is not None

    configs = await chat_service.build_tools_config(fetched, db=db)
    assert len(configs) == 1
    entry = configs[0]
    assert entry["definition_key"] == "naver_search_blog"
    assert entry["credentials"] == {"client_id": "nv-id", "client_secret": "nv-secret"}
    assert entry["credential_id"] == str(tool_cred.id)
    assert entry["parameters"] == {"query": "moldy"}


# ---------------------------------------------------------------------------
# Scenario 2: trigger executor uses the same prefetch helper, so the legacy
# prefetch-skew bug (chat router prefetch ≠ trigger prefetch) is closed.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_executor_uses_chat_service_prefetch() -> None:
    async with TestSession() as db:
        model = await _seed_user_and_model(db)
        agent = Agent(
            user_id=TEST_USER_ID,
            name="Trig Agent",
            system_prompt="ok",
            model_id=model.id,
        )
        db.add(agent)
        await db.flush()

        trigger = AgentTrigger(
            agent_id=agent.id,
            user_id=TEST_USER_ID,
            trigger_type="interval",
            schedule_config={"interval_minutes": 10},
            input_message="run me",
            status="active",
            run_count=0,
        )
        db.add(trigger)
        await db.commit()
        trigger_id = trigger.id

    with (
        patch(
            "app.agent_runtime.trigger_executor.get_agent_with_tools_proxy",
            create=True,
        ),
        patch(
            "app.agent_runtime.trigger_executor.execute_agent_invoke",
            return_value="ok",
        ),
        patch(
            "app.agent_runtime.trigger_executor.async_session",
            TestSession,
        ),
        patch(
            "app.services.chat_service.get_agent_with_tools",
            wraps=chat_service.get_agent_with_tools,
        ) as spy,
    ):
        from app.agent_runtime.trigger_executor import execute_trigger

        await execute_trigger(str(trigger_id))

    spy.assert_called_once()
    # The 3 positional args must include the trigger's user_id (closes the
    # legacy "trigger executor used different prefetch" bug).
    args = spy.call_args.args
    assert args[2] == TEST_USER_ID


# ---------------------------------------------------------------------------
# Scenario 2b: get_owned_conversation_with_agent (W3-out retrospective MED
# follow-up) — single join + agent runtime eager-load chain. SELECT 수가
# 기존 (get_conversation + get_agent_with_tools) 조합보다 정확히 1 적어야
# 한다. round-trip 절감의 정량 회귀 가드.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owned_conv_with_agent_saves_one_roundtrip() -> None:
    from sqlalchemy import event

    from app.models.conversation import Conversation

    # Seed 는 fresh session 1개에서, 측정은 별도 fresh session 2개에서 — 같은
    # session 의 identity_map 캐시가 SELECT 를 우회해 카운트가 왜곡되는 것을
    # 차단한다.
    async with TestSession() as setup_db:
        model = await _seed_user_and_model(setup_db)
        agent = Agent(
            user_id=TEST_USER_ID,
            name="RT Agent",
            system_prompt="x",
            model_id=model.id,
        )
        setup_db.add(agent)
        await setup_db.flush()
        conv = Conversation(agent_id=agent.id, title="RT Conv")
        setup_db.add(conv)
        await setup_db.commit()
        conv_id = conv.id

    select_count_old = 0
    select_count_new = 0

    def _on_cursor_old(_conn, _cursor, statement: str, *_args: Any) -> None:
        nonlocal select_count_old
        if statement.lstrip().lower().startswith("select"):
            select_count_old += 1

    def _on_cursor_new(_conn, _cursor, statement: str, *_args: Any) -> None:
        nonlocal select_count_new
        if statement.lstrip().lower().startswith("select"):
            select_count_new += 1

    async with TestSession() as old_db:
        engine = old_db.get_bind()
        event.listen(engine, "before_cursor_execute", _on_cursor_old)
        try:
            old_conv = await chat_service.get_conversation(old_db, conv_id)
            assert old_conv is not None
            old_agent = await chat_service.get_agent_with_tools(
                old_db, old_conv.agent_id, TEST_USER_ID
            )
            assert old_agent is not None
        finally:
            event.remove(engine, "before_cursor_execute", _on_cursor_old)

    async with TestSession() as new_db:
        engine = new_db.get_bind()
        event.listen(engine, "before_cursor_execute", _on_cursor_new)
        try:
            new_conv = await chat_service.get_owned_conversation_with_agent(
                new_db, conv_id, TEST_USER_ID
            )
            assert new_conv is not None
            assert new_conv.agent is not None
            assert new_conv.agent.model is not None
        finally:
            event.remove(engine, "before_cursor_execute", _on_cursor_new)

    # 정확히 1 적어야 함 (conv lookup + agent base 두 SELECT 가 단일 join SELECT
    # 로 통합). selectin chain (model/llm_credential/tool_links/mcp_tool_links/
    # skill_links) 는 양쪽 동일하게 발사되므로 차이는 정확히 -1.
    assert select_count_new == select_count_old - 1, (
        f"old={select_count_old}, new={select_count_new} — "
        "단일 join 으로 정확히 1 query 절감 invariant 위반"
    )


# ---------------------------------------------------------------------------
# Scenario 3: MCP probe pulls credential fields through the unified
# interpolation path (no shadow `env_var_resolver` module).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_connect_and_list_interpolates_credentials() -> None:
    """The MCP probe runs ``resolve_deep`` against the credential payload so
    ``Authorization: Bearer ${credential.token}`` is hydrated before it hits
    the wire."""

    captured_headers: dict[str, str] = {}

    class _FakeServerInfo:
        name = "fake"
        version = "1"

    class _FakeInitResult:
        serverInfo = _FakeServerInfo()

    class _FakeTool:
        name = "echo"
        description = "echoes"
        inputSchema = {"type": "object"}

    class _FakeToolsResult:
        tools = [_FakeTool()]

    class _FakeSession:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc) -> None:
            return None

        async def initialize(self):
            return _FakeInitResult()

        async def list_tools(self):
            return _FakeToolsResult()

    def _fake_streamable(url, headers=None):
        captured_headers["url"] = url
        captured_headers.update(headers or {})

        class _Conn:
            async def __aenter__(self):
                return (None, None, None)

            async def __aexit__(self, *_exc):
                return None

        return _Conn()

    with (
        patch("mcp.client.session.ClientSession", _FakeSession),
        patch("mcp.client.streamable_http.streamablehttp_client", _fake_streamable),
    ):
        result = await mcp_client.connect_and_list(
            transport="streamable_http",
            url="https://mcp.example.com/api",
            headers={"Authorization": "=Bearer {{ $credentials.token }}"},
            credentials={"token": "secret-token"},
        )

    assert result["success"] is True
    assert result["server_info"]["name"] == "fake"
    assert captured_headers["Authorization"] == "Bearer secret-token"


@pytest.mark.asyncio
async def test_mcp_connect_and_list_handles_unknown_transport() -> None:
    """Truly unknown transports surface the documented unsupported error.

    ``stdio`` is now wired up (M-MCP1c S17a) so probe it with a real
    ``connect_and_list`` call by passing an unsupported transport string
    instead.
    """

    result = await mcp_client.connect_and_list(transport="websocket", url=None)
    assert result["success"] is False
    assert "not supported" in result["error"]


@pytest.mark.asyncio
async def test_mcp_connect_and_list_stdio_requires_command() -> None:
    """The new stdio branch surfaces a friendly error when ``command`` is
    missing rather than spawning a half-configured subprocess."""

    result = await mcp_client.connect_and_list(transport="stdio", url=None)
    assert result["success"] is False
    assert "command" in (result["error"] or "").lower()
