"""Real scoped chat steers delivered before PostgreSQL worker handoff."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import checkpointer
from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph
from app.agent_runtime.recovery_stream import continue_protocol_delivery
from app.agent_runtime.run_recovery_checkpoint import (
    RecoveryCheckpointBlocked,
    verified_resume_config,
)
from app.agent_runtime.runtime_config import AgentConfig
from app.chat_recovery.fence import claim_run_owner, run_fence_scope
from app.models.conversation_run import ConversationRun, ConversationRunToolReceipt
from app.models.conversation_run_steer import ConversationRunSteer
from app.models.message_event import MessageEvent
from app.models.organization import Organization, Tenant
from app.services.conversation_run_recovery_checkpoint import _recovery_head_and_cursor
from app.services.conversation_run_steer_events import RunSteerReceiptFeed
from app.services.trace_storage import load_events
from app.tenancy.checkpoint_audit import run_configurable
from tests.authz.unit_scope_fixture import unit_context
from tests.integration.steer_recovery_worker import persist, probe_graph
from tests.integration.test_chat_steer_concurrency import BACKEND_ROOT, ActiveChat, active_chat

pytestmark = pytest.mark.integration

__all__ = ["active_chat"]  # share the authorized PG/HTTP fixture


async def wait_until(proc: asyncio.subprocess.Process, boundary: str) -> None:
    assert proc.stdout and proc.stderr
    async with asyncio.timeout(90):
        while line := await proc.stdout.readline():
            if line.decode().strip() == f"BREAK:{boundary}":
                return
        raise AssertionError((await proc.stderr.read()).decode())


@asynccontextmanager
async def old_worker(
    chat: ActiveChat, mode: str, log: Path
) -> AsyncIterator[asyncio.subprocess.Process]:
    url = make_url(os.environ["INTEGRATION_DATABASE_URL"])
    async with chat.sessions() as db:
        run = await db.get(ConversationRun, chat.run_id, with_for_update=True)
        assert run
        claim_run_owner(run, "worker")
        await db.commit()
    env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG") if key in os.environ}
    env.update(
        DATABASE_URL=url.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False),
        DATABASE_URL_SYNC=url.set(drivername="postgresql").render_as_string(hide_password=False),
        ENCRYPTION_KEYS=os.environ["ENCRYPTION_KEYS"],
        CHAT_STEER_ENABLED="true",
        MOLDY_DISABLE_ENV_FILE="true",
        PYTHON_DOTENV_DISABLED="1",
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.integration.steer_recovery_worker",
        str(chat.run_id),
        mode,
        str(log),
        cwd=BACKEND_ROOT,
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        yield proc
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()


async def journal(chat: ActiveChat) -> list[dict[str, Any]]:
    async with chat.sessions() as db:
        turn = await db.scalar(
            select(MessageEvent).where(
                MessageEvent.conversation_id == chat.conversation_id,
                MessageEvent.assistant_msg_id == str(chat.run_id),
            )
        )
        return await load_events(db, turn) if turn else []


def receipt_ids(events: list[dict[str, Any]]) -> list[str]:
    return [
        e["id"]
        for e in events
        if e["method"] == "custom" and e["data"].get("name") == "moldy.run_steer"
    ]


async def reject_mismatched_receipt(
    db: AsyncSession, run: ConversationRun, cfg: AgentConfig, steer_id: UUID
) -> None:
    """The checkpoint's valid message ID and digest cannot acknowledge another scope's row."""
    suffix = uuid4().hex
    tenant = Tenant(name="Other company", slug=suffix, secrets_namespace=suffix)
    db.add(tenant)
    await db.flush()
    org = Organization(tenant_id=tenant.id, name="Other organization", slug=suffix)
    previous = ConversationRun(
        conversation_id=run.conversation_id,
        source="chat",
        status="completed",
        user_id=run.user_id,
        agent_id=run.agent_id,
        tenant_id=run.tenant_id,
        org_id=run.org_id,
    )
    db.add_all([org, previous])
    await db.flush()
    for foreign in ({"run_id": previous.id}, {"tenant_id": tenant.id, "org_id": org.id}):
        restore = await db.begin_nested()
        try:
            await db.execute(
                update(ConversationRunSteer)
                .where(ConversationRunSteer.id == steer_id)
                .values(**foreign)
            )
            with pytest.raises(RecoveryCheckpointBlocked, match="recovery_steer_unconfirmed"):
                await _recovery_head_and_cursor(db, run, cfg)
        finally:
            await restore.rollback()


@pytest.mark.parametrize("mode", ["confirmed", "unconfirmed"])
async def test_recover_current_graph_after_steer_boundary(
    active_chat: ActiveChat, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    chat = active_chat
    # Replace the suite's SQLite unit-session override with this lane's real PG pool.
    monkeypatch.setattr("app.database.async_session", chat.sessions)
    monkeypatch.setattr("app.chat_recovery.fence.async_session", chat.sessions)
    root = str(chat.conversation_id)
    context = unit_context(chat.user_id)
    config: RunnableConfig = {
        "configurable": run_configurable(root, chat.run_id, context.tenant_id)
    }
    ids: list[str] = []
    log = tmp_path / "effects.jsonl"
    dsn = make_url(os.environ["INTEGRATION_DATABASE_URL"]).set(drivername="postgresql")
    async with AsyncPostgresSaver.from_conn_string(
        dsn.render_as_string(hide_password=False)
    ) as saver:
        await saver.setup()
        monkeypatch.setattr(checkpointer, "get_checkpointer", lambda: saver)
        async with old_worker(chat, mode, log) as old:
            await wait_until(old, "lookup-first")
            for instruction in ("limit to Korea", "compare prices"):
                response = await chat.send(uuid4(), instruction)
                assert response.status_code == 200, response.text
                ids.append(response.json()["id"])
            # The production receipt feed polls while the first read is in flight.
            # The next graph update follows this barrier after its refresh interval.
            await asyncio.sleep(1.1)
            assert old.stdin
            old.stdin.write(b"continue\n")
            await old.stdin.drain()
            await wait_until(old, "lookup-second" if mode == "confirmed" else "model")
            async with asyncio.timeout(30):
                # The process running the graph owns a different event loop.
                while not {f"{steer}:steer:pending" for steer in ids} <= set(  # noqa: ASYNC110
                    receipt_ids(await journal(chat))
                ):
                    await asyncio.sleep(0.05)
        async with chat.sessions() as db:
            run = await db.get(ConversationRun, chat.run_id, with_for_update=True)
            assert run
            cfg = AgentConfig(
                provider="openai",
                model_name="local",
                api_key=None,
                base_url=None,
                system_prompt="",
                tools_config=[],
                thread_id=root,
                org_context=context,
                agent_id=str(run.agent_id),
                user_id=str(run.user_id),
            )
            if mode == "unconfirmed":
                with pytest.raises(RecoveryCheckpointBlocked, match="recovery_steer_unconfirmed"):
                    await _recovery_head_and_cursor(db, run, cfg)
                rows = (
                    await db.scalars(
                        select(ConversationRunSteer)
                        .where(ConversationRunSteer.run_id == chat.run_id)
                        .order_by(ConversationRunSteer.sequence)
                    )
                ).all()
                assert [str(row.id) for row in rows] == ids
                assert all(
                    row.status == "pending" and row.delivery_started_at is not None for row in rows
                )
                records = [json.loads(line) for line in log.read_text().splitlines()]
                assert [r["lookup"] for r in records if "lookup" in r] == ["first"]
                return
            await reject_mismatched_receipt(db, run, cfg, UUID(ids[0]))
            checkpoint_id, cursor = await _recovery_head_and_cursor(db, run, cfg)
            generation, token = run.owner_generation, run.owner_token
            fence = claim_run_owner(run, "worker")
            assert fence.generation == generation + 1 and fence.owner_token != token
            await db.commit()

        async def save(events: list[dict[str, Any]]) -> None:
            await persist(fence, events)

        with run_fence_scope(fence), continue_protocol_delivery(cursor):
            graph = probe_graph(saver, log, phase="successor")
            exact = {"configurable": {**config["configurable"], "checkpoint_id": checkpoint_id}}
            checked = await verified_resume_config(
                graph, exact, replace(cfg, checkpoint_id=checkpoint_id), str(chat.run_id)
            )
            feed = RunSteerReceiptFeed.for_chat(
                context, root, str(chat.run_id), source="recovery", profile="standard"
            )
            assert feed is not None
            errs: list[Any] = []
            async for _ in stream_agent_response_langgraph(
                graph,
                None,
                checked,
                run_id=str(chat.run_id),
                persist_callback=save,
                steer_receipts=feed,
                error_sink=errs,
            ):
                pass
            assert errs == []
        records = [json.loads(line) for line in log.read_text().splitlines()]
        reads = [(r["lookup"], r["worker"]) for r in records if "lookup" in r]
        assert reads == [("first", "confirmed"), ("second", "successor")]
        notes = ["compare the records", "limit to Korea", "compare prices"]
        assert [r["humans"] for r in records if "model" in r] == [[notes[0]], notes, notes]
        receipts = await chat.client.get(chat.receipt_url)
        assert receipts.status_code == 200
        assert [(r["id"], r["status"]) for r in receipts.json()] == [
            (steer, "applied") for steer in ids
        ]
        async with chat.sessions() as db:
            tool_rows = (
                await db.scalars(
                    select(ConversationRunToolReceipt)
                    .where(ConversationRunToolReceipt.run_id == chat.run_id)
                    .order_by(ConversationRunToolReceipt.tool_call_id)
                )
            ).all()
            assert [
                (r.tool_call_id, r.owner_generation, r.status, r.effect_kind) for r in tool_rows
            ] == [
                ("lookup-first", generation, "completed", "read"),
                ("lookup-second", fence.generation, "completed", "read"),
            ]
            assert [r.result_json["content"] if r.result_json else None for r in tool_rows] == [
                r["result"] for r in records if "lookup" in r
            ]
        events = await journal(chat)
        assert sorted(receipt_ids(events)) == sorted(
            f"{i}:steer:{s}" for i in ids for s in ("pending", "applied")
        )
        phases = [
            (e["data"]["event"], e["data"]["tool_call_id"])
            for e in events
            if e["method"] == "tools"
            and e["data"].get("event") in {"tool-started", "tool-finished"}
        ]
        for stage in ("first", "second"):
            for action in ("tool-started", "tool-finished"):
                assert phases.count((action, f"lookup-{stage}")) == 1
