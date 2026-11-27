"""Measure repeated async Deep Agents compactions using Moldy's production scoped filesystem.

From backend/ run `uv run python -m evals.chat_archive.benchmark --mode legacy`.
Use --mode both for the paired adoption test. Each row runs in a fresh process.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import platform
import re
import resource
import statistics
import subprocess
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import NAMESPACE_DNS, UUID, uuid5

from deepagents.middleware.summarization import SummarizationMiddleware, SummarizationState
from langchain.agents.middleware.types import (
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
)
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, AnyMessage, get_buffer_string
from langgraph.runtime import ExecutionInfo, Runtime

from app.agent_runtime.offload_storage import OffloadIdentity, ScopedOffloadStorage
from evals.chat_archive.corpus import TOPICS, messages_for_round

ROUNDS = 4
HEADER = re.compile(rb"(?m)^## Summarized at \d{4}-\d{2}-\d{2}T[^\n]+\n\n")


async def respond(request: ModelRequest) -> ModelResponse:
    return ModelResponse(result=[AIMessage(content="결과 확인")])


def peak_rss_bytes() -> int:
    size = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return size if sys.platform == "darwin" else size * 1024


def stored_bytes(folder: Path) -> int:
    return sum(path.stat().st_size for path in folder.rglob("*") if path.is_file())


async def measure(case: int, mode: str, folder: Path) -> dict[str, Any]:
    backend = ScopedOffloadStorage(
        data_dir=folder,
        identity=OffloadIdentity(
            owner_id="synthetic-benchmark", conversation_id=f"case-{case}", run_id="measure"
        ),
    ).for_actor(UUID(int=100))
    model = FakeListChatModel(responses=["도구 결과와 인용을 요약했습니다."])
    middleware_type = SummarizationMiddleware
    if mode == "candidate":
        from app.agent_runtime.archive_summarization import ArchiveSummarizationMiddleware

        middleware_type = ArchiveSummarizationMiddleware
    middleware = middleware_type(
        model=model,
        backend=backend,
        trigger=("messages", 6),
        keep=("messages", 2),
        trim_tokens_to_summarize=None,
    )
    messages: list[AnyMessage] = []
    state: SummarizationState = {"messages": messages}
    archive_path: str | None = None
    cutoffs = [0]
    durations = []
    for period in range(ROUNDS):
        messages.extend(messages_for_round(case, period))
        identity = f"archive-benchmark.{case}.{period}.moldy.test"
        runtime = Runtime(
            execution_info=ExecutionInfo(
                thread_id=f"case-{case}",
                run_id="measure",
                checkpoint_ns="",
                checkpoint_id=str(uuid5(NAMESPACE_DNS, identity)),
                task_id=identity,
            )
        )
        request = ModelRequest(model=model, state=state, messages=messages, runtime=runtime)
        start = perf_counter()
        result = await middleware.awrap_model_call(request, respond)
        durations.append(perf_counter() - start)
        if not (
            isinstance(result, ExtendedModelResponse)
            and result.command
            and isinstance(result.command.update, dict)
        ):
            raise RuntimeError("Expected the real middleware to emit a checkpoint update")
        update = result.command.update
        if "messages" in update:
            raise RuntimeError("Unexpected replacement of the controlled conversation input")
        event = update["_summarization_event"]
        state["_summarization_event"] = event
        state["_summarization_session_id"] = update["_summarization_session_id"]
        cutoffs.append(event["cutoff_index"])
        archive_path = event["file_path"]
    peak = peak_rss_bytes()  # Capture runtime high-water mark before verifier allocations.
    if not isinstance(archive_path, str):
        raise RuntimeError("Compaction completed without an archive reference")

    original = [message for i in range(ROUNDS) for message in messages_for_round(case, i)]
    raw_hash = hashlib.sha256()
    for message in original[: cutoffs[-1]]:
        raw_hash.update(message.model_dump_json().encode())
        raw_hash.update(b"\n")
    if mode == "legacy":
        downloaded = (await backend.adownload_files([archive_path]))[0]
        if downloaded.error or downloaded.content is None:
            raise RuntimeError("Archive could not be restored")
        archive_size = len(downloaded.content)
        sections = HEADER.split(downloaded.content)
        if len(sections) != ROUNDS + 1 or sections[0] != b"":
            raise RuntimeError("Archive did not contain every compaction")
        expected, actual = hashlib.sha256(), hashlib.sha256()
        for start, stop, section in zip(cutoffs[:-1], cutoffs[1:], sections[1:], strict=True):
            body = get_buffer_string(original[start:stop], format="xml").encode() + b"\n\n"
            expected.update(body)
            actual.update(section)
    else:
        expected, actual = raw_hash, hashlib.sha256()
        archive_size = 0
        for block in backend.iter_archive_bytes(archive_path):
            actual.update(block)
            archive_size += len(block)
    return {
        "case": case,
        "mode": mode,
        "rounds": ROUNDS,
        "peak_rss_bytes": peak,
        "compaction_seconds": durations,
        "archive_bytes": archive_size,
        "source_sha256": raw_hash.hexdigest(),
        "source_messages": cutoffs[-1],
        "disk_bytes": stored_bytes(folder),
        "expected_sha256": expected.hexdigest(),
        "restored_sha256": actual.hexdigest(),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    durations = sorted(t for row in rows for t in row["compaction_seconds"])
    return {
        "cases": len(rows),
        "restored": sum(row["expected_sha256"] == row["restored_sha256"] for row in rows),
        "rss_median_bytes": statistics.median(r["peak_rss_bytes"] for r in rows),
        "compaction_p95_seconds": durations[math.ceil(len(durations) * 0.95) - 1],
        "total_archive_bytes": sum(r["archive_bytes"] for r in rows),
        "total_disk_bytes": sum(r["disk_bytes"] for r in rows),
    }


def adoption_gate(rows: list[dict[str, Any]], summaries: dict[str, Any]) -> dict[str, Any]:
    legacy, candidate = summaries["legacy"], summaries["candidate"]
    rss_ratio = candidate["rss_median_bytes"] / legacy["rss_median_bytes"]
    latency_ratio = candidate["compaction_p95_seconds"] / legacy["compaction_p95_seconds"]

    def sources(mode: str) -> dict[int, str]:
        return {r["case"]: r["source_sha256"] for r in rows if r["mode"] == mode}

    quality = (
        len(rows) == 2 * len(TOPICS)
        and sources("legacy") == sources("candidate")
        and legacy["restored"] == candidate["restored"] == len(TOPICS)
    )
    return {
        "comparable_and_restored": quality,
        "peak_rss_reduction_pct": round(100 * (1 - rss_ratio), 2),
        "compaction_p95_change_pct": round(100 * (latency_ratio - 1), 2),
        "adopt_chunked_default": quality and rss_ratio <= 0.80 and latency_ratio <= 1.10,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("legacy", "candidate", "both"), default="both")
    parser.add_argument("--cases", type=int, choices=range(1, len(TOPICS) + 1), default=len(TOPICS))
    parser.add_argument("--worker", type=int, choices=range(len(TOPICS)))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.worker is not None:
        if args.mode == "both":
            parser.error("--worker requires exactly one mode")
        with tempfile.TemporaryDirectory() as directory:
            print(json.dumps(asyncio.run(measure(args.worker, args.mode, Path(directory)))))
        return
    modes = ["legacy", "candidate"] if args.mode == "both" else [args.mode]
    rows = []
    for case in range(args.cases):
        for mode in modes if case % 2 == 0 else modes[::-1]:
            result = subprocess.run(  # noqa: S603 -- fixed module and validated case/mode
                [
                    sys.executable,
                    "-m",
                    "evals.chat_archive.benchmark",
                    "--mode",
                    mode,
                    "--worker",
                    str(case),
                ],
                env={**os.environ, "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"},
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode:
                raise RuntimeError(f"{mode} case {case} failed: {result.stderr}")
            rows.append(json.loads(result.stdout))
            print(f"{mode} {case + 1}/{args.cases}", file=sys.stderr, flush=True)
    summaries = {mode: summarize([r for r in rows if r["mode"] == mode]) for mode in modes}
    report = {
        "environment": {
            "platform": platform.system(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "deepagents": version("deepagents"),
            "langgraph": version("langgraph"),
            "measurement": "scoped filesystem, local fake model",
        },
        "summary": summaries,
    }
    if args.mode == "both":
        report["gate"] = adoption_gate(rows, summaries)
    print(json.dumps(report, indent=2))
    if args.output:
        args.output.write_text(json.dumps({**report, "rows": rows}, indent=2) + "\n")
    if any(entry["restored"] != args.cases for entry in summaries.values()):
        raise SystemExit("A restored conversation did not match its input")


if __name__ == "__main__":
    main()
