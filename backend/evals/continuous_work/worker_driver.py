"""Run the fixed quotation cases through the ordinary HTTP chat and PostgreSQL worker.

From backend: python -m evals.continuous_work.worker_driver --output paired.jsonl
Use --case to run a subset. Only a scripted loopback model is called. Its token unit
is a contiguous word or punctuation character in the message/tool JSON. The catalog
rate is illustrative; model-provider charges for this experiment are zero.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import psycopg
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from psycopg.rows import dict_row

from evals.continuous_work.cases import (
    FILE_NAME,
    SCENARIOS,
    Scenario,
    goal_request,
    meets_task,
    task_text,
)
from evals.continuous_work.paired_stack import PairedStack, paired_stack

_CONTINUE = "같은 원본 견적과 파일을 다시 대조해 틀리거나 빠진 부분을 고쳐 주세요."
_DONE = "비교 작업을 완료했습니다."
_TERMINAL = {"completed", "failed", "stale", "canceled", "interrupted"}


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _units(value: object) -> int:
    rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return len(re.findall(r"\w+|[^\w\s]", rendered))


@dataclass
class QuoteScript:
    case: Scenario
    conversation_id: str
    turn: int = 0
    accepted_file: str | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def answer(self, request: dict[str, Any]) -> tuple[dict[str, Any], str, dict[str, int]]:
        messages = request["messages"]
        last = messages[-1]
        names = {tool["function"]["name"] for tool in request.get("tools", [])}
        if last["role"] == "tool":
            operation = "acknowledge"
            # A failed filesystem operation is an observable worker result. Never treat
            # the claimed file as written or hide failure by generating a substitute.
            content = str(last.get("content", ""))
            self.calls[-1]["tool_result"] = content[:1000]
            if not re.search(r"error|denied|failed|does not exist|not found", content, re.I):
                self.accepted_file = self.case.csv_at(self.turn)
            message = {"role": "assistant", "content": _DONE}
        else:
            if last["role"] != "user":
                raise ValueError(f"unexpected final role {last['role']}")
            self.turn += 1
            desired = self.case.csv_at(self.turn)
            operation = "answer"
            message = {"role": "assistant", "content": _DONE}
            if desired is not None and desired != self.accepted_file:
                path = f"/conversations/{self.conversation_id}/{FILE_NAME}"
                if self.accepted_file:
                    operation = "edit_file"
                    arguments = {
                        "file_path": path,
                        "old_string": self.accepted_file.rstrip("\n"),
                        "new_string": desired.rstrip("\n"),
                    }
                else:
                    operation = "write_file"
                    arguments = {"file_path": path, "content": desired}
                if operation not in names:
                    raise ValueError(f"{operation} not offered by worker")
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_" + uuid4().hex,
                            "type": "function",
                            "function": {
                                "name": operation,
                                "arguments": json.dumps(arguments, ensure_ascii=False),
                            },
                        }
                    ],
                }
        finish = "tool_calls" if "tool_calls" in message else "stop"
        inp = _units([messages, request.get("tools", [])])
        out = _units(message)
        self.calls.append(
            {
                "turn": self.turn,
                "operation": operation,
                "input_tokens": inp,
                "output_tokens": out,
                "declared_done": message.get("content") == _DONE,
            }
        )
        return (
            message,
            finish,
            {"prompt_tokens": inp, "completion_tokens": out, "total_tokens": inp + out},
        )


@asynccontextmanager
async def gateway() -> AsyncIterator[tuple[str, dict[str, QuoteScript]]]:
    active: dict[str, QuoteScript] = {}
    app = FastAPI()

    @app.post("/v1/chat/completions", response_model=None)
    async def chat(request: dict[str, Any]) -> JSONResponse | StreamingResponse:
        script = active.get("case")
        if script is None:
            raise HTTPException(400, "No active evaluator conversation")
        try:
            message, finish, usage = script.answer(request)
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc
        envelope = {
            "id": "chatcmpl-" + uuid4().hex,
            "created": 1,
            "model": request["model"],
        }
        if not request.get("stream"):
            return JSONResponse(
                {
                    **envelope,
                    "object": "chat.completion",
                    "choices": [{"index": 0, "finish_reason": finish, "message": message}],
                    "usage": usage,
                }
            )
        delta = dict(message)
        if "tool_calls" in delta:
            delta["tool_calls"] = [{"index": 0, **call} for call in delta["tool_calls"]]
        prefix = {**envelope, "object": "chat.completion.chunk"}
        chunks = [
            {**prefix, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {**prefix, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
            {**prefix, "choices": [], "usage": usage},
        ]
        wire = "".join("data: " + json.dumps(frame) + "\n\n" for frame in chunks)
        return StreamingResponse(iter([wire + "data: [DONE]\n\n"]), media_type="text/event-stream")

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if task.done():
                        await task
                        raise RuntimeError("fixture stopped before startup")
                    await asyncio.sleep(0.02)
            yield f"http://127.0.0.1:{port}/v1", active
        finally:
            server.should_exit = True
            await task


async def submit(client: httpx.AsyncClient, cid: str, content: str) -> str:
    key = str(uuid4())
    response = await client.post(
        f"/api/conversations/{cid}/langgraph/threads/{cid}/commands",
        json={
            "id": key,
            "method": "run.start",
            "params": {
                "client_request_id": key,
                "input": {"messages": [{"role": "user", "content": content}]},
            },
        },
    )
    return str(response.raise_for_status().json()["result"]["run_id"])


async def read_run_rows(conn: psycopg.AsyncConnection[Any], cid: str) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """SELECT r.id::text AS id, r.status, r.error_code, u.prompt_tokens, u.completion_tokens,
            u.estimated_cost FROM conversation_runs r LEFT JOIN token_usages u ON u.run_id=r.id
            WHERE r.conversation_id=%s ORDER BY r.created_at, r.id""",
        (cid,),
    )
    return await cursor.fetchall()


async def read_files(client: httpx.AsyncClient, cid: str) -> dict[str, dict[str, Any]]:
    base = f"/api/conversations/{cid}/artifacts"
    artifacts = (await client.get(base)).raise_for_status().json()
    versions = {}
    for item in artifacts:
        if item["path"] != FILE_NAME or item["status"] != "ready":
            continue
        response = await client.get(f"{base}/{item['id']}/download")
        data = response.raise_for_status().content
        sha = _digest(data)
        if sha != item["sha256"]:
            raise RuntimeError("downloaded bytes did not match artifact digest")
        versions[item["run_id"]] = {
            "sha256": sha,
            "csv_text": data.decode("utf-8"),
            "version_id": item["version_id"],
        }
    return versions


async def read_pinned_goal_result(
    stack: PairedStack,
    goal_url: str,
    goal: dict[str, Any],
    conn: psycopg.AsyncConnection[Any],
    artifacts: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if goal.get("status") != "completed":
        return None
    # This experiment registers one quotation file. Follow the version on its
    # completion receipt even when an artifact now exposes another current version.
    evidence = goal["last_evidence"]
    (check,) = evidence["checks"]
    if not evidence["all_passed"] or not check["passed"]:
        raise ValueError("completed goal has no passing evidence")
    version_id = check["version_id"]
    run_id = check["source_run_id"]
    artifact_id = check["artifact_id"]
    version_path = f"{goal_url}/artifacts/{artifact_id}/versions"
    response = await stack.client.get(f"{version_path}/{version_id}/download")
    data = response.raise_for_status().content
    digest = _digest(data)
    version = await (
        await conn.execute(
            """SELECT v.sha256, a.assistant_msg_id AS run_id FROM artifact_versions v
                JOIN conversation_artifacts a ON a.id = v.artifact_id
                WHERE v.id = %s AND a.id = %s""",
            (version_id, artifact_id),
        )
    ).fetchone()
    if (
        not version
        or version["run_id"] != run_id
        or digest != check["sha256"]
        or digest != version["sha256"]
    ):
        raise ValueError("pinned version bytes or producing run disagree with evidence")
    text = data.decode("utf-8")
    # Repaired cases produced another version on the first failed attempt. The
    # same artifact URL must reject that unrelated version, despite common ownership.
    unrelated = next(
        (item["version_id"] for key, item in artifacts.items() if key != run_id),
        None,
    )
    wrong_status = None
    if unrelated is not None:
        wrong_status = (await stack.client.get(f"{version_path}/{unrelated}/download")).status_code
        if wrong_status != 404:
            raise ValueError("artifact/version mismatch unexpectedly returned a result")
    return {
        "run_id": run_id,
        "version_id": version_id,
        "sha256": digest,
        "csv_text": text,
        "matches_reference": meets_task(text),
        "wrong_version_control_status": wrong_status,
    }


async def execute_case(
    stack: PairedStack,
    active: dict[str, QuoteScript],
    conn: psycopg.AsyncConnection[Any],
    case: Scenario,
    arm: str,
) -> dict[str, Any]:
    client = stack.client
    conv = await client.post(
        f"/api/agents/{stack.agent_id}/conversations", json={"title": case.slug}
    )
    cid = conv.raise_for_status().json()["id"]
    script = QuoteScript(case, cid)
    active["case"] = script
    goal_url = f"/api/conversations/{cid}/goal"
    if arm == "goal":
        (await client.put(goal_url, json=goal_request())).raise_for_status()

    await submit(client, cid, task_text())
    manual = 0
    goal: dict[str, Any] = {}
    error: str | None = None
    async with asyncio.timeout(150):
        while True:
            runs = await read_run_rows(conn, cid)
            if not runs or runs[-1]["status"] not in _TERMINAL:
                await asyncio.sleep(0.15)
                continue
            final = runs[-1]
            if final["status"] != "completed":
                error = final["error_code"] or final["status"]
                break
            if arm == "goal":
                goal = (await client.get(goal_url)).raise_for_status().json()["goal"]
                if goal["status"] in {"blocked", "completed", "paused", "canceled"}:
                    break
            else:
                outputs = await read_files(client, cid)
                latest = next((outputs[r["id"]] for r in reversed(runs) if r["id"] in outputs), {})
                # One criterion means a failed file has zero verified progress. The
                # same three unsuccessful judgements end either arm (or four total
                # turns if progress elsewhere were possible).
                if meets_task(latest.get("csv_text")) or len(runs) >= 3:
                    break
                await submit(client, cid, _CONTINUE)
                manual += 1
            await asyncio.sleep(0.15)

    if arm == "goal" and not goal:
        goal = (await client.get(goal_url)).raise_for_status().json()["goal"]
    runs = await read_run_rows(conn, cid)
    artifacts = await read_files(client, cid)
    attempts = await (
        await conn.execute(
            """SELECT ga.run_id::text AS id, ga.status, ga.evidence
                FROM goal_attempts ga JOIN conversation_goals cg ON cg.id=ga.goal_id
                WHERE cg.conversation_id=%s ORDER BY ga.created_at, ga.id""",
            (cid,),
        )
    ).fetchall()
    assessed = []
    for row in attempts:
        evidence = row["evidence"] or {}
        checks = evidence.get("checks", [])
        assessed.append(
            {
                "run_id": row["id"],
                "status": row["status"],
                "passed": evidence.get("all_passed"),
                "reason": checks[0]["reason"] if checks else None,
                "version_sha256": checks[0].get("sha256") if checks else None,
            }
        )
    history = (await client.get(f"/api/conversations/{cid}/messages")).raise_for_status().json()
    visible_declarations = sum(
        message["role"] == "assistant" and message["content"] == _DONE
        for message in history["messages"]
    )
    output_versions = []
    last_file_run = None
    for ordinal, run_row in enumerate(runs, 1):
        run_id = run_row["id"]
        version = artifacts.get(run_id, {})
        if version:
            last_file_run = run_id
        output_versions.append(
            {
                "run_id": run_id,
                "sha256": None,
                "csv_text": None,
                **version,
                "visible_file_source_run_id": last_file_run,
                "model_declared_done": any(
                    c["turn"] == ordinal and c["declared_done"] for c in script.calls
                ),
            }
        )
    costs = [r["estimated_cost"] for r in runs]
    return {
        "case": case.slug,
        "arm": arm,
        "task_sha256": _digest(task_text().encode()),
        "run_ids": [r["id"] for r in runs],
        "followups_manual": manual,
        "followups_automatic": goal.get("auto_followups", 0),
        "input_tokens": sum(r["prompt_tokens"] or 0 for r in runs),
        "output_tokens": sum(r["completion_tokens"] or 0 for r in runs),
        "usage_rows": sum(r["prompt_tokens"] is not None for r in runs),
        "usage_per_run": [
            {
                "run_id": r["id"],
                "input_tokens": r["prompt_tokens"],
                "output_tokens": r["completion_tokens"],
                "projected_cost_usd": str(r["estimated_cost"])
                if r["estimated_cost"] is not None
                else None,
            }
            for r in runs
        ],
        "projected_cost_usd": str(sum(costs, Decimal(0))) if None not in costs else None,
        "pinned_goal_result": await read_pinned_goal_result(stack, goal_url, goal, conn, artifacts),
        "goal_status": goal.get("status"),
        "goal_blocked_reason": goal.get("blocked_reason"),
        "error": error,
        "versions": output_versions,
        "history_visible_completions": visible_declarations,
        "provider_usage_matches_ledger": (
            all(r["prompt_tokens"] is not None and r["completion_tokens"] is not None for r in runs)
            and sum(c["input_tokens"] for c in script.calls)
            == sum(r["prompt_tokens"] for r in runs)
            and sum(c["output_tokens"] for c in script.calls)
            == sum(r["completion_tokens"] for r in runs)
        ),
        "goal_attempts": assessed,
        "provider_calls": script.calls,
    }


async def run(path: Path, cases: list[str]) -> None:
    selected = [case for case in SCENARIOS if not cases or case.slug in cases]
    if not selected or set(cases) - {case.slug for case in selected}:
        raise ValueError("unknown case")
    async with (
        gateway() as (endpoint, active),
        paired_stack(endpoint) as stack,
        await psycopg.AsyncConnection[dict[str, Any]].connect(
            stack.postgres_dsn, row_factory=dict_row, autocommit=True
        ) as conn,
    ):
        with path.open("x", encoding="utf-8") as handle:
            for i, case in enumerate(selected):
                arms = ("ordinary", "goal") if i % 2 == 0 else ("goal", "ordinary")
                for arm in arms:
                    row = await execute_case(stack, active, conn, case, arm)
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                    handle.flush()
                    print(case.slug, arm, row["goal_status"], row["error"], flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--case", action="append", default=[])
    args = parser.parse_args()
    asyncio.run(run(args.output, args.case))
