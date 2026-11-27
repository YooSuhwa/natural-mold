"""Test worker killed with an open PostgreSQL saver, before the UI receipt is updated."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any
from uuid import UUID

from deepagents import create_deep_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.engine import make_url

from app.agent_runtime.chat_steer_middleware import RunSteerMiddleware
from app.schemas.conversation_graph_input import STEER_RECEIPTS_CHANNEL
from app.tenancy.checkpoint_audit import run_configurable


async def write_checkpoint(conversation: UUID, run: UUID, tenant: UUID, boundary: str) -> None:
    dsn = make_url(os.environ["DATABASE_URL_SYNC"])
    if not (dsn.database or "").startswith("moldy_pg_lane_") or boundary not in ("before", "after"):
        raise ValueError("A disposable PostgreSQL lane and an explicit breakpoint are required")
    calls: list[list[str | list[str | dict]]] = []

    class ObservedModel(FakeMessagesListChatModel):
        def bind_tools(self, tools: Any, **kwargs: Any) -> ObservedModel:
            return self

        def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> Any:
            calls.append(
                [message.content for message in messages if isinstance(message, HumanMessage)]
            )
            return super()._generate(messages, *args, **kwargs)

    async with AsyncPostgresSaver.from_conn_string(
        dsn.render_as_string(hide_password=False)
    ) as saver:
        await saver.setup()
        graph = create_deep_agent(
            model=ObservedModel(responses=[AIMessage(content="found it")]),
            middleware=[RunSteerMiddleware()],
            checkpointer=saver,
        )
        config: RunnableConfig = {
            "configurable": {
                **run_configurable(str(conversation), run, tenant),
                "checkpoint_ns": "",
            }
        }
        await graph.ainvoke(
            {"messages": [HumanMessage(content="compare sources")]},
            config,
            interrupt_before=["model"] if boundary == "before" else None,
            interrupt_after=["model"] if boundary == "after" else None,
            durability="sync",
        )
        saved = await saver.aget_tuple(config)
        assert saved is not None
        receipt = saved.checkpoint["channel_values"][STEER_RECEIPTS_CHANNEL]
        print(
            json.dumps(
                {"calls": calls, "staged": sorted(receipt["staged"]), "confirmed": receipt["ids"]}
            ),
            flush=True,
        )
        # Simulate worker death at this committed graph boundary. Do not run the
        # next graph node, acknowledge the SQL receipt or close the saver cleanly.
        os._exit(75)


if __name__ == "__main__":
    asyncio.run(
        write_checkpoint(UUID(sys.argv[1]), UUID(sys.argv[2]), UUID(sys.argv[3]), sys.argv[4])
    )
