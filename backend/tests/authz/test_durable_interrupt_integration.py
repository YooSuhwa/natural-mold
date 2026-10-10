"""A-10: reconnect a real Postgres checkpointer before resuming auth_required."""

import os
from uuid import uuid4

import pytest
from deepagents import create_deep_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from psycopg import AsyncConnection, sql
from psycopg.rows import DictRow, dict_row
from sqlalchemy.engine import make_url

from tests.authz.runtime_prototype import ConnectionGuard, ScriptedModel

pytestmark = pytest.mark.integration


async def test_auth_interrupt_survives_closed_connection_and_rebuilt_graph() -> None:
    # Given: a dedicated schema inside the runner-owned throwaway database.
    url = (
        make_url(os.environ["INTEGRATION_DATABASE_URL"])
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )
    schema = "authz_proto_" + uuid4().hex
    calls: list[str] = []

    @tool
    def action() -> str:
        """Record one authenticated action."""
        calls.append("action")
        return "ok"

    config: RunnableConfig = {"configurable": {"thread_id": "durable-auth"}}
    async with await AsyncConnection.connect(url, autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            async with await AsyncConnection[DictRow].connect(
                url, autocommit=True, row_factory=dict_row
            ) as conn:
                await conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
                saver = AsyncPostgresSaver(conn)
                await saver.setup()
                graph = create_deep_agent(
                    model=ScriptedModel(
                        responses=[
                            AIMessage(
                                content="",
                                tool_calls=[{"name": "action", "args": {}, "id": "durable-call"}],
                            )
                        ]
                    ),
                    tools=[action],
                    middleware=[ConnectionGuard(auth_tools=frozenset({"action"}), ready=False)],
                    checkpointer=saver,
                )
                first = await graph.ainvoke({"messages": [HumanMessage(content="act")]}, config)
                interrupt_id = first["__interrupt__"][0].id
                assert calls == []
            # When: the HTTP/DB connection and graph are reconstructed after authentication.
            async with await AsyncConnection[DictRow].connect(
                url, autocommit=True, row_factory=dict_row
            ) as conn:
                await conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
                graph = create_deep_agent(
                    model=ScriptedModel(responses=[AIMessage(content="finished")]),
                    tools=[action],
                    middleware=[ConnectionGuard(auth_tools=frozenset({"action"}), ready=True)],
                    checkpointer=AsyncPostgresSaver(conn),
                )
                snapshot = await graph.aget_state(config)
                assert snapshot.tasks[0].interrupts[0].id == interrupt_id
                final = await graph.ainvoke(
                    Command(resume={"type": "respond", "message": "retry"}), config
                )
                # Then: the persisted call resumes once, rather than rerunning the model request.
                assert "__interrupt__" not in final
                assert calls == ["action"]
        finally:
            await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
