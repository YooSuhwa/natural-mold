"""Start one acceptance worker with a local yield observer on the scripted model."""

from __future__ import annotations

import json
import math
import os
import re
import runpy
import sys
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from importlib.util import find_spec
from pathlib import Path
from types import CodeType
from uuid import UUID

SAMPLE = re.compile(
    r"\bE2E_AGUI_PERF_([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})\b"
)


@contextmanager
def observe_model_yields(ledger: Path) -> Iterator[None]:
    from app.agent_runtime.e2e_scripted_model import (
        SLOW_STREAM_MARKER,
        SLOW_STREAM_PARTS,
        E2EScriptedChatModel,
    )
    from langchain_core.outputs import ChatGenerationChunk

    code = E2EScriptedChatModel._stream.__code__
    parts = {}
    chars = 0
    for index, text in enumerate(SLOW_STREAM_PARTS):
        if text in parts:
            raise ValueError("The slow-stream fixture requires unique chunks for correlation")
        chars += len(text)
        parts[text] = (index, chars)

    monitoring = sys.monitoring
    # IDs 3 and 4 are available for tools without a reserved monitoring ID.
    tool_id = next(index for index in (4, 3) if monitoring.get_tool(index) is None)
    fd = os.open(ledger, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)

    def capture(yielding: CodeType, _offset: int, chunk: object) -> None:
        epoch_ns = time.time_ns()
        monotonic_ns = time.monotonic_ns()
        if yielding is not code or not isinstance(chunk, ChatGenerationChunk):
            return
        content = chunk.message.content
        if not isinstance(content, str) or content not in parts:
            return
        # PY_YIELD runs inside the original generator before its caller receives the chunk.
        prompt = sys._getframe(1).f_locals.get("human_text")
        if not isinstance(prompt, str) or SLOW_STREAM_MARKER not in prompt:
            return
        match = SAMPLE.search(prompt)
        if not match:
            return
        index, length = parts[content]
        row = {
            "sample_id": match[1],
            "ordinal": index + 1,
            "cumulative_chars": length,
            "sourceEpochMs": epoch_ns / 1_000_000,
            "source_monotonic_ns": str(monotonic_ns),
        }
        os.write(fd, (json.dumps(row, separators=(",", ":")) + "\n").encode("ascii"))

    try:
        monitoring.use_tool_id(tool_id, "moldy-acceptance-scripted-yield")
        try:
            monitoring.register_callback(tool_id, monitoring.events.PY_YIELD, capture)
            monitoring.set_local_events(tool_id, code, monitoring.events.PY_YIELD)
            yield
        finally:
            monitoring.set_local_events(tool_id, code, 0)
            monitoring.register_callback(tool_id, monitoring.events.PY_YIELD, None)
            monitoring.free_tool_id(tool_id)
    finally:
        os.close(fd)


def model_timing(sample_id: UUID, ledger: Path) -> list[dict[str, int | float]]:
    """Return the observed source yields only, in fixture order."""
    if not ledger.exists():
        return []
    times: dict[int, float] = {}
    with ledger.open() as stream:
        for line in stream:
            row = json.loads(line)
            if row["sample_id"] != str(sample_id):
                continue
            ordinal, stamp = row["ordinal"], row["sourceEpochMs"]
            if (
                type(ordinal) is not int
                or not 1 <= ordinal <= 6
                or ordinal in times
                or type(stamp) not in {int, float}
                or not math.isfinite(stamp)
                or stamp <= 0
            ):
                raise ValueError("Invalid or repeated scripted model yield")
            times[ordinal] = stamp
    return [{"ordinal": ordinal, "sourceEpochMs": times[ordinal]} for ordinal in sorted(times)]


def main() -> None:
    if sys.argv[1:2] not in (["uvicorn"], ["app.jobs.worker"]):
        raise SystemExit("Expected uvicorn or app.jobs.worker with its usual arguments")
    if not (
        os.environ.get("E2E_LANE") == "scripted"
        and os.environ.get("E2E_SCRIPTED_MODEL_ENABLED") == "true"
        and os.environ.get("MOLDY_DISABLE_ENV_FILE") == "true"
        and os.environ.get("APP_ENV", "dev") == "dev"
    ):
        raise SystemExit("The model timing observer requires an isolated scripted lane")
    ledger = Path(os.environ["MOLDY_AGUI_MODEL_TIMINGS"])
    if not ledger.is_absolute():
        raise SystemExit("MOLDY_AGUI_MODEL_TIMINGS must be an absolute local path")
    # Match `python -m` module resolution from the isolated backend's working directory.
    sys.path.insert(0, str(Path.cwd()))
    del sys.argv[0]
    module = sys.argv[0]
    with ExitStack() as probes:
        probes.enter_context(observe_model_yields(ledger))
        if module == "uvicorn" and find_spec("app.services.ag_ui_read") is not None:
            from perf_read import observe_read_durations

            probes.enter_context(
                observe_read_durations(ledger.with_name(ledger.name + ".read.jsonl"))
            )
        runpy.run_module(module, run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
