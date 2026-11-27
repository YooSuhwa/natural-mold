"""Run the existing v3 HTTP contract with the original 0.7.32 backend and graph."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator
from importlib.metadata import version
from typing import Any
from unittest.mock import patch
from uuid import UUID

from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.engine import make_url


async def run(args: argparse.Namespace) -> dict[str, Any]:
    from ag_ui_legacy_child_graph import build_graph, config

    # The disposable application database and the checkpointer must be the same lane.
    dsn = make_url(os.environ["AG_UI_LEGACY_CHECKPOINT_DSN"])
    if not dsn.database or not dsn.database.startswith("moldy_pg_lane_"):
        raise ValueError("An isolated integration database is required")
    os.environ["DATABASE_URL"] = dsn.set(drivername="postgresql+asyncpg").render_as_string(
        hide_password=False
    )
    os.environ["DATABASE_URL_SYNC"] = dsn.set(drivername="postgresql").render_as_string(
        hide_password=False
    )
    os.environ.update(
        E2E_SCRIPTED_MODEL_ENABLED="true",
        LANGSMITH_TRACING="false",
        LANGCHAIN_TRACING_V2="false",
        RATE_LIMIT_ENABLED="false",
        PREPARED_RUN_LEASE_ENABLED="false",
        JWT_SECRET="old-v3-disposable-control-fixture-key",
    )
    legacy = os.environ.get("MOLDY_LEGACY_BACKEND")
    if legacy:
        sys.path.insert(0, legacy)
    assert (version("deepagents"), version("langgraph")) == ("0.7.32", "1.2.19")

    from app.agent_runtime import checkpointer, langgraph_agent_stream_runner
    from app.agent_runtime.runtime_config import AgentConfig
    from app.auth.cookies import ACTIVE_ORG_COOKIE
    from app.auth.jwt import create_access_token, create_csrf_token
    from app.config import settings
    from app.database import async_session, engine
    from app.main import create_app
    from app.models.conversation import Conversation
    from app.models.conversation_run import ConversationRun
    from app.services.conversation_run_worker import get_run_task_registry

    async with async_session() as db:
        conversation = await db.get(Conversation, args.conversation_id)
        assert conversation is not None and conversation.user_id == args.user_id
        tenant_id = conversation.tenant_id

    csrf = create_csrf_token(args.user_id)
    path = f"/api/conversations/{args.conversation_id}/langgraph/threads/{args.conversation_id}"
    effects: list[str] = []
    commands: list[tuple[UUID, object]] = []
    async with (
        AsyncPostgresSaver.from_conn_string(os.environ["DATABASE_URL_SYNC"]) as saver,
        AsyncClient(
            transport=ASGITransport(app=create_app()),
            base_url="http://fixture",
            headers={"X-CSRF-Token": csrf},
            cookies={
                settings.cookie_name_access: create_access_token(args.user_id),
                settings.cookie_name_csrf: csrf,
                ACTIVE_ORG_COOKIE: str(args.org_id),
            },
        ) as client,
    ):
        graph = build_graph(saver, args.child_name, effects)

        async def run_fixture_graph(
            cfg: AgentConfig, *, stream_input: Command, run_id: str, **kwargs: Any
        ) -> AsyncIterator[str]:
            commands.append((UUID(run_id), stream_input.resume))
            assert cfg.thread_id == str(args.conversation_id)
            result = await graph.ainvoke(stream_input, config(cfg.thread_id, run_id, tenant_id))
            assert not result.get("__interrupt__")
            yield 'event: message_end\ndata: {"content":"Reviewer done.","usage":{}}\n\n'

        # Replace just the provider-backed graph with the same deterministic graph
        # that produced this checkpoint. The real HTTP handler and native worker run.
        with (
            patch.object(checkpointer, "_checkpointer", saver),
            patch.object(
                langgraph_agent_stream_runner, "_run_langgraph_agent_stream", run_fixture_graph
            ),
        ):
            snapshot = await client.get(f"{path}/state")
            assert snapshot.status_code == 200, snapshot.text[:400]
            slots = [
                slot
                for task in snapshot.json()["tasks"]
                for slot in task["interrupts"]
                if slot["id"] == args.interrupt_id
            ]
            assert len(slots) == 1
            namespace = slots[0]["ns"]
            answer = {"decisions": [{"type": "approve"}]}
            response = await client.post(
                f"{path}/commands",
                json={
                    "id": "legacy-v3-approval",
                    "method": "input.respond",
                    "params": {
                        "responses": [
                            {
                                "namespace": namespace,
                                "interrupt_id": args.interrupt_id,
                                "response": answer,
                            }
                        ]
                    },
                },
            )
            body = response.json()
            assert response.status_code == 200 and body["type"] == "success", body
            child_run_id = UUID(body["result"]["run_id"])
            worker = get_run_task_registry().get(child_run_id)
            if worker is not None:
                await asyncio.wait_for(worker, 20)
            assert commands == [(child_run_id, {args.interrupt_id: answer})], commands
            current = await graph.aget_state(config(args.conversation_id, child_run_id, tenant_id))
            assert effects == ["draft"] and not current.interrupts

    async with async_session() as db:
        child = (
            await db.scalars(select(ConversationRun).where(ConversationRun.id == child_run_id))
        ).one()
        assert child.parent_run_id == args.run_id and child.status == "completed"
    await engine.dispose()
    return {
        "accepted": True,
        "effects": len(effects),
        "pending": len(current.interrupts),
        "namespace": namespace,
        "workerStatus": child.status,
        "runId": str(child_run_id),
        "deepagents": version("deepagents"),
        "langgraph": version("langgraph"),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("conversation-id", "user-id", "org-id", "run-id"):
        parser.add_argument(f"--{name}", type=UUID, required=True)
    parser.add_argument("--child-name", required=True)
    parser.add_argument("--interrupt-id", required=True)
    print(json.dumps(asyncio.run(run(parser.parse_args())), separators=(",", ":")))
