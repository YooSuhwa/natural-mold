"""Time a steer from the timestamp of its committed receipt to the next model invocation.

From backend, supply a throwaway, migrated, local PostgreSQL 16 database:

    MOLDY_DISABLE_ENV_FILE=true PYTHON_DOTENV_DISABLED=1 E2E_SCRIPTED_MODEL_ENABLED=true \
    CHAT_STEER_ENABLED=true \
    DATABASE_URL=postgresql+asyncpg://USER@localhost:PORT/moldy_pg_lane_goal_latency \
    DATABASE_URL_SYNC=postgresql://USER@localhost:PORT/moldy_pg_lane_goal_latency \
    ENCRYPTION_KEYS="$(openssl rand -hex 32)" python -m evals.continuous_work.measure_steers \
    --output /tmp/steer-latency.json

Use temporary HOME and data directories. Each repetition creates a new synthetic account,
conversation and run. Only the scripted model and one waiting gate in the real, read-only
current_datetime tool are instrumented. The ordinary HTTP command, worker, middleware and
Postgres checkpointer all run. No paid model is required.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import shutil
import subprocess
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from time import perf_counter_ns
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.messages import AIMessageChunk, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.tools import StructuredTool
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agent_runtime import checkpointer, tool_factory
from app.agent_runtime.chat_steer_model_delivery import steer_message_id
from app.agent_runtime.e2e_scripted_model import E2EScriptedChatModel
from app.agent_runtime.model_provider_registry import PROVIDER_MAP
from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.auth.jwt import create_access_token, create_csrf_token
from app.config import settings
from app.database import async_session, engine
from app.main import create_app
from app.models.agent import Agent
from app.models.model import Model
from tests.authz.unit_scope_fixture import TEST_ORG_ID
from tests.integration.test_conversation_queue_concurrency import _seed_conversation


@dataclass
class RunProbe:
    tool_started: asyncio.Event = field(default_factory=asyncio.Event)
    release_tool: asyncio.Event = field(default_factory=asyncio.Event)
    inputs: list[list[str]] = field(default_factory=list)
    positions: list[list[str | None]] = field(default_factory=list)
    tool_result_seen: list[bool] = field(default_factory=list)
    model_times: list[datetime] = field(default_factory=list)
    monotonic_times: list[int] = field(default_factory=list)
    tool_calls: int = 0
    clock_result: str | None = None

    def model(self) -> type[E2EScriptedChatModel]:
        probe = self

        class ObservedModel(E2EScriptedChatModel):
            async def _astream(
                self,
                messages: list[BaseMessage],
                stop: list[str] | None = None,
                run_manager: AsyncCallbackManagerForLLMRun | None = None,
                **kwargs: Any,
            ) -> AsyncIterator[ChatGenerationChunk]:
                probe.model_times.append(datetime.now(UTC))
                probe.monotonic_times.append(perf_counter_ns())
                probe.inputs.append([m.text for m in messages if isinstance(m, HumanMessage)])
                probe.positions.append(
                    [
                        m.id if isinstance(m, HumanMessage) else m.tool_call_id
                        for m in messages
                        if isinstance(m, HumanMessage | ToolMessage)
                    ]
                )
                probe.tool_result_seen.append(
                    any(
                        isinstance(m, ToolMessage)
                        and m.tool_call_id == "clock"
                        and m.status == "success"
                        and m.content == probe.clock_result
                        for m in messages
                    )
                )
                if len(probe.inputs) == 1:
                    chunk = AIMessageChunk(
                        content="",
                        tool_call_chunks=[
                            {"index": 0, "name": "current_datetime", "id": "clock", "args": "{}"}
                        ],
                    )
                else:
                    chunk = AIMessageChunk(content="지시와 조회 결과를 확인했습니다.")
                yield ChatGenerationChunk(message=chunk)

        return ObservedModel

    def clock(self) -> StructuredTool:
        tool = tool_factory._build_current_datetime_tool()  # noqa: SLF001 - gate the actual clock
        if not isinstance(tool, StructuredTool) or not tool.coroutine:
            raise TypeError("Expected the async registered clock tool")
        clock = tool.coroutine

        async def wait_and_read() -> str:
            self.tool_calls += 1
            self.tool_started.set()
            await asyncio.wait_for(self.release_tool.wait(), 20)
            self.clock_result = await clock()
            return self.clock_result

        tool.coroutine = wait_and_read
        return tool


def check_lane() -> None:
    url = make_url(settings.database_url)
    sync = make_url(settings.database_url_sync)
    if not (
        url.host in ("127.0.0.1", "localhost")
        and url.database == "moldy_pg_lane_goal_latency"
        and sync.set(drivername=url.drivername) == url
        and os.getenv("MOLDY_DISABLE_ENV_FILE", "").lower() == "true"
        and settings.chat_steer_enabled
        and settings.e2e_scripted_model_enabled
        and not settings.prepared_run_lease_enabled
    ):
        raise ValueError(
            "Use a migrated disposable local goal latency lane and the scripted model."
        )


async def trial(number: int) -> dict[str, Any]:
    probe = RunProbe()
    user_id, agent_id, conversation_id = await _seed_conversation(
        async_sessionmaker(engine, expire_on_commit=False)
    )
    async with async_session() as db:
        agent = await db.get_one(Agent, agent_id)
        model = await db.get_one(Model, agent.model_id)
        model.provider = "e2e_scripted"
        await db.commit()

    csrf = create_csrf_token(user_id)
    cookies = {
        settings.cookie_name_access: create_access_token(user_id),
        settings.cookie_name_csrf: csrf,
        ACTIVE_ORG_COOKIE: str(TEST_ORG_ID),
    }
    base = f"/api/conversations/{conversation_id}"
    directives = [f"범위 {number} 국내 업체만", f"범위 {number} 공식 견적만"]
    with (
        patch.dict(PROVIDER_MAP, {"e2e_scripted": probe.model()}),
        patch.dict(tool_factory._BUILTIN_BUILDERS, {"builtin:current_datetime": probe.clock}),  # noqa: SLF001
    ):
        async with AsyncClient(
            transport=ASGITransport(app=create_app()),
            base_url="http://test",
            headers={"X-CSRF-Token": csrf},
            cookies=cookies,
        ) as client:
            body = {
                "id": str(uuid4()),
                "method": "run.start",
                "params": {"input": {"messages": [{"role": "user", "content": "업체 비교"}]}},
            }
            start = await client.post(
                f"{base}/langgraph/threads/{conversation_id}/commands", json=body
            )
            start.raise_for_status()
            result = start.json()
            if "run_id" not in result.get("result", {}):
                raise RuntimeError(f"Run was not accepted: {result}")
            run_url = f"{base}/runs/{result['result']['run_id']}"
            try:
                await asyncio.wait_for(probe.tool_started.wait(), 7)
            except TimeoutError as exc:
                status = await client.get(run_url)
                raise RuntimeError(
                    f"Clock tool did not start (run status {status.json().get('status')})"
                ) from exc
            receipts = []
            ack_times = []
            try:
                for directive in directives:
                    response = await client.post(
                        f"{run_url}/steers",
                        json={"client_request_id": str(uuid4()), "text": directive},
                    )
                    response.raise_for_status()
                    ack_times.append(perf_counter_ns())
                    receipts.append(response.json())
                require([row["sequence"] for row in receipts] == [1, 2], "Unordered receipts")
                require(
                    all(row["status"] == "pending" for row in receipts), "Unexpected receipt state"
                )
            finally:
                probe.release_tool.set()
            async with asyncio.timeout(20):
                while True:
                    response = await client.get(run_url)
                    response.raise_for_status()
                    status = response.json()["status"]
                    if status not in ("queued", "running", "accepted", "pending"):
                        break
                    await asyncio.sleep(0.025)
            require(status == "completed", f"Run ended with {status}")
            require(
                probe.inputs == [["업체 비교"], ["업체 비교", *directives]], "Incorrect model input"
            )
            require(probe.tool_calls == 1, "Clock tool executed an unexpected number of times")
            require(
                probe.tool_result_seen == [False, True], "Tool output missing at model boundary"
            )
            instruction_ids = [steer_message_id(UUID(row["id"])) for row in receipts]
            require(probe.positions[1][1:] == ["clock", *instruction_ids], "Misplaced input IDs")
            # Terminal status and the checkpoint-based receipt reconciliation commit separately.
            # Poll only to verify durable delivery. The measured endpoint was already captured.
            checks = 0
            expected = [(row["id"], "applied") for row in receipts]
            async with asyncio.timeout(5):
                while True:
                    response = await client.get(f"{run_url}/steers")
                    response.raise_for_status()
                    checks += 1
                    rows = response.json()
                    if [(row["id"], row["status"]) for row in rows] == expected:
                        break
                    require(
                        all(row["status"] in ("pending", "applied") for row in rows),
                        "Receipt moved to a different run or needs review",
                    )
                    await asyncio.sleep(0.025)
    observed = probe.model_times[1]
    created = [datetime.fromisoformat(row["created_at"]).replace(tzinfo=UTC) for row in receipts]
    durations = [round((observed - at).total_seconds() * 1000, 3) for at in created]
    ack_durations = [round((probe.monotonic_times[1] - at) / 1_000_000, 3) for at in ack_times]
    require(all(ms >= 0 for ms in durations + ack_durations), "Invalid clock interval")
    return {
        "trial": number,
        "receipt_to_model_ms": durations,
        "ack_to_model_ms": ack_durations,
        "post_terminal_receipt_reads": checks,
    }


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


async def run(repetitions: int, source_sha: str) -> dict[str, Any]:
    check_lane()
    async with async_session() as db:
        pg = await db.scalar(text("SHOW server_version"))
        require(str(pg).startswith("16."), "Expected PostgreSQL 16")
    await checkpointer.init_checkpointer(settings.database_url_sync)
    try:
        trials = []
        for index in range(repetitions):
            try:
                trials.append(await trial(index + 1))
            except Exception as exc:
                trials.append({"trial": index + 1, "error": str(exc)[:1200]})
    finally:
        await checkpointer.shutdown_checkpointer()
        await engine.dispose()
    samples = [value for row in trials for value in row.get("receipt_to_model_ms", [])]
    return {
        "schema_version": 1,
        "source_sha": source_sha,
        "driver": "httpx ASGITransport, signed user cookie + CSRF, no browser/network proxy",
        "checkpointer": "AsyncPostgresSaver",
        "concurrency": "one run at a time; one current_datetime tool per run",
        "network_model_calls": 0,
        "postgres": pg,
        "deepagents": version("deepagents"),
        "langgraph": version("langgraph"),
        "setting": "one ordinary chat worker; two directives admitted while its clock tool waits",
        "start": "UTC created_at of accepted SQL receipt (same host; set before commit)",
        "end": "model call containing matching IDs and text after the clock tool result",
        "companion": "monotonic interval from HTTP acknowledgment to entering the model",
        "attempted_runs": repetitions,
        "planned_steers": repetitions * 2,
        "verified_ordered_once_and_applied": len(samples),
        "failed_runs": sum("error" in row for row in trials),
        "p95_method": "nearest rank; both admissions share the next invocation within each run",
        "receipt_to_model_p95_ms": sorted(samples)[math.ceil(len(samples) * 0.95) - 1]
        if samples
        else None,
        "trials": trials,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=24, choices=range(1, 101))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    git = shutil.which("git")
    if not git:
        parser.error("git is required for source provenance")
    sha = subprocess.check_output([git, "rev-parse", "HEAD"], text=True).strip()  # noqa: S603
    data = asyncio.run(run(args.repetitions, sha))
    args.output.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print({k: data[k] for k in ("attempted_runs", "failed_runs", "receipt_to_model_p95_ms")})
    raise SystemExit(bool(data["failed_runs"]))
