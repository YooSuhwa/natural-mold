"""Black-box AG-UI acceptance on two live Moldy workers and an isolated PostgreSQL database.

Run through the cutover stack with its synthetic company accounts. On success
the JSON evidence includes a capture for the independent TypeScript SDK verifier.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import traceback
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import httpx
import psycopg
from http_common import (
    Gate,
    GateFailure,
    checked_sse,
    expect_status,
    open_gate,
    require,
)


def _answer(events: list[dict[str, Any]]) -> tuple[str, str]:
    parts: dict[str, list[str]] = {}
    for event in events:
        kind = event["type"]
        if kind in ("TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_CHUNK") and not event.get(
            "subagentRunId"
        ):
            parts.setdefault(event["messageId"], []).append(event.get("delta", ""))
    require(len(parts) == 1, "expected exactly one assistant answer")
    mid, chunks = next(iter(parts.items()))
    return mid, "".join(chunks)


def _public_events(
    events: list[dict[str, Any]], tid: str, rid: str, private: str
) -> None:
    require(
        events[0]["type"] == "RUN_STARTED" and events[-1]["type"] == "RUN_FINISHED",
        "incomplete normal run",
    )
    starts = [e for e in events if e["type"] == "RUN_STARTED"]
    finishes = [e for e in events if e["type"] == "RUN_FINISHED"]
    require(len(starts) == len(finishes) == 1, "duplicate normal lifecycle")
    for e in (starts[0], finishes[0]):
        require((e["threadId"], e["runId"]) == (tid, rid), "public ID mismatch")
    require(
        starts[0].get("protocolVersion") == "1.0",
        "producer omitted its supported version",
    )
    require(
        (finishes[0].get("outcome") or {"type": "success"})["type"] == "success",
        "normal run did not succeed",
    )
    dump = json.dumps(events, ensure_ascii=False)
    require(
        private not in dump
        and '"rawEvent"' not in dump
        and '"moldy.raw_event"' not in dump,
        "private state reached the public protocol",
    )


async def _counts(gate: Gate, thread_id: str) -> dict[str, int]:
    rows = await gate.sql(
        """
        WITH room AS (SELECT %s::uuid AS id)
        SELECT
            (SELECT count(*) FROM conversation_runs WHERE conversation_id=room.id) AS runs,
            (SELECT count(*) FROM ag_ui_command_receipts WHERE conversation_id=room.id) AS receipts,
            (SELECT count(*) FROM message_events WHERE conversation_id=room.id) AS journals,
            (SELECT count(*) FROM message_event_chunks WHERE conversation_id=room.id) AS chunks,
            (SELECT count(*) FROM token_usages WHERE conversation_id=room.id) AS usage_rows,
            (SELECT count(*) FROM conversation_run_inputs WHERE conversation_id=room.id) AS inputs,
            (SELECT count(*) FROM skill_usage_events WHERE conversation_id=room.id) AS skill_effects
        FROM room
        """,
        (thread_id,),
    )
    return rows[0]


async def _recoverable_cursor(
    gate: Gate,
    thread_id: str,
    public: str,
    canonical: tuple[str, str],
) -> dict[str, object]:
    base = f"/api/conversations/{thread_id}/ag-ui"
    frames: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        query = {"runId": public}
        if after is not None:
            query["after"] = after
        data = await gate.owner_b.json("GET", f"{base}/events?{urlencode(query)}")
        batch = data["frames"]
        require(len(frames) < 5000, "durable replay did not reach a terminal boundary")
        frames.extend(batch)
        if not data.get("hasMore", False):
            require(data["terminal"] is True, "completed run replay was non-terminal")
            break
        require(batch and batch[-1]["id"] != after, "cursor did not advance")
        after = batch[-1]["id"]
    ids = [f["id"] for f in frames]
    require(
        len(ids) >= 4 and len(set(ids)) == len(ids),
        "lost, duplicate or missing frame cursors",
    )
    require(
        _answer([f["event"] for f in frames]) == canonical,
        "persisted journal changed the public answer",
    )
    # Resume in the middle of a single logical answer, not just after a run boundary.
    midpoint = len(ids) // 2
    suffix: list[str] = []
    after = ids[midpoint]
    while True:
        query = urlencode({"runId": public, "after": after})
        result = await gate.owner_a.json("GET", f"{base}/events?{query}")
        batch = result["frames"]
        suffix.extend(frame["id"] for frame in batch)
        if not result.get("hasMore", False):
            require(result["terminal"] is True, "suffix missing terminal state")
            break
        require(bool(batch) and len(suffix) < 5000, "suffix cursor did not advance")
        after = batch[-1]["id"]
    require(
        suffix == ids[midpoint + 1 :], "exclusive cursor skipped or repeated frames"
    )
    unknown = await gate.owner_b.send(
        "GET",
        f"{base}/events?{urlencode({'runId': public, 'after': 'missing-cursor'})}",
    )
    expect_status(unknown, 409)
    require("AG_UI_SNAPSHOT_REQUIRED" in unknown.text, "missing recovery explanation")
    return {
        "durable_frames": len(ids),
        "suffix_frames": len(suffix),
        "invalid_cursor": unknown.status_code,
    }


async def check_start_and_recover(gate: Gate) -> dict[str, object]:
    private = f"internal fixture {uuid4().hex}"
    _, tid = await gate.create_chat(
        "wire deduplication", agent_overrides={"system_prompt": private}
    )
    rid = f"request-{uuid4().hex}"
    path = f"/api/conversations/{tid}/ag-ui/runs"
    request = {
        "threadId": tid,
        "runId": rid,
        "protocolVersion": "1.0",
        "messages": [
            {"id": f"msg-{uuid4().hex}", "role": "user", "content": "E2E_SLOW_STREAM"}
        ],
    }
    require((await _counts(gate, tid))["runs"] == 0, "fresh chat already has a run")
    forbidden = await gate.owner_a.send("POST", path, request, csrf=False)
    expect_status(forbidden, 403)
    foreign_submit = await gate.other_b.send("POST", path, request)
    expect_status(foreign_submit, 404)
    denied_counts = await _counts(gate, tid)
    require(
        denied_counts["runs"] == denied_counts["receipts"] == 0,
        "denied writes accepted a command",
    )

    # Both incoming POSTs target different processes with the same persisted request identity.
    ra, rb = await asyncio.gather(
        gate.owner_a.send("POST", path, request),
        gate.owner_b.send("POST", path, request),
    )
    transcripts = [checked_sse(ra), checked_sse(rb)]
    for response, events in zip((ra, rb), transcripts, strict=True):
        require(
            response.headers.get("X-Run-Id") == rid, "public HTTP run identity changed"
        )
        _public_events(events, tid, rid, private)
    answer = _answer(transcripts[0])
    require(
        answer == _answer(transcripts[1]), "duplicate start produced different messages"
    )
    require(
        answer[1] == "E2E slow stream completed after detached navigation.",
        "scripted model final text changed",
    )
    sql_runs = await gate.sql(
        "SELECT id, status FROM conversation_runs WHERE conversation_id=%s", (tid,)
    )
    require(
        len(sql_runs) == 1 and sql_runs[0]["status"] == "completed",
        "duplicate start did not converge to one completed physical run",
    )
    physical = str(sql_runs[0]["id"])
    receipts = await gate.sql(
        """
        SELECT r.public_run_id, r.tenant_id, r.projection_version, coalesce(r.run_id, i.run_id) AS id
        FROM ag_ui_command_receipts AS r LEFT JOIN conversation_run_inputs AS i ON i.id=r.input_id
        WHERE r.conversation_id=%s
        """,
        (tid,),
    )
    require(
        len(receipts) == 1
        and receipts[0]["public_run_id"] == rid
        and str(receipts[0]["tenant_id"]) == gate.tenant_id
        and str(receipts[0]["id"]) == physical
        and bool(receipts[0]["projection_version"]),
        "accepted request failed to bind exactly one versioned, company-scoped receipt",
    )

    for response in (ra, rb):
        if header := response.headers.get("X-Moldy-Run-Id"):
            require(header == physical, "physical response header not the original run")
    baseline = await _counts(gate, tid)
    require(
        baseline["runs"] == 1 and baseline["journals"] == 1 and baseline["chunks"] > 0,
        "missing durable conversation effects",
    )
    different = {
        **request,
        "messages": [{**request["messages"][0], "content": "different body"}],
    }
    conflict = await gate.owner_b.send("POST", path, different)
    expect_status(conflict, 409)
    replay = checked_sse(await gate.owner_a.send("POST", path, request))
    require(_answer(replay) == answer, "accepted request could not be safely replayed")
    reads = f"/api/conversations/{tid}/ag-ui"
    state_path = f"{reads}/snapshot?{urlencode({'runId': rid})}"
    state = await gate.owner_b.json("GET", state_path)
    require(
        state["threadId"] == tid
        and state["runId"] == rid
        and state["latestPhysicalRunId"] == physical
        and state["activeRunId"] is None
        and state["physicalRunId"] is None,
        "opposite worker snapshot lost public to physical association",
    )
    require(state["runStatus"] == "completed", "snapshot does not expose completed run")
    require(
        private not in json.dumps(state, ensure_ascii=False),
        "snapshot exposed system prompt",
    )
    for read in [
        state_path,
        f"{reads}/runs/{rid}/stream",
        f"{reads}/events?runId={rid}",
    ]:
        denied = await gate.other_b.send("GET", read)
        missing = await gate.other_b.send("GET", read.replace(tid, str(uuid4())))
        expect_status(denied, 404)
        expect_status(missing, 404)
        require(
            denied.json() == missing.json(),
            "foreign read distinguishes a private resource",
        )
    recovered = checked_sse(
        await gate.owner_b.send("GET", f"{reads}/runs/{rid}/stream")
    )
    _public_events(recovered, tid, rid, private)
    require(
        _answer(recovered) == answer,
        "read-only recovery lost original answer ID or text",
    )
    cursor = await _recoverable_cursor(gate, tid, rid, answer)
    final_counts = await _counts(gate, tid)
    require(
        final_counts == baseline,
        "retry, recovery or denied read changed durable effect counts",
    )

    # This synthetic capture also feeds the independent TS HttpAgent and React probes.
    expectation = {
        "threadId": tid,
        "runId": rid,
        "protocolVersion": "1.0",
        "terminal": "success",
        "messages": [{"id": answer[0], "role": "assistant", "content": answer[1]}],
        "forbiddenStrings": [private],
    }
    for label, events in (("start", transcripts[0]), ("recover", recovered)):
        out = gate.evidence_dir / f"http-{label}-transcript.json"
        out.write_text(
            json.dumps(
                {"name": label, "events": events, "expect": expectation},
                ensure_ascii=False,
            )
            + "\n"
        )
    return {
        "public_run": rid,
        "physical_run": physical,
        "thread": tid,
        "counts": final_counts,
        "cursor": cursor,
        "replayed_message": answer[0],
        "conflict": 409,
        "foreign_read": 404,
    }


async def check_detach(gate: Gate) -> dict[str, object]:
    """Abandon the creator's real HTTP request while its worker is producing output."""
    _, thread = await gate.create_chat("detach active sender")
    public = f"detach-{uuid4().hex}"
    base = f"/api/conversations/{thread}/ag-ui"
    body = {
        "threadId": thread,
        "runId": public,
        "protocolVersion": "1.0",
        "messages": [
            {"id": f"msg-{uuid4().hex}", "role": "user", "content": "E2E_SLOW_STREAM"}
        ],
    }
    partial = ""
    async with gate.owner_a.client.stream(
        "POST", f"{base}/runs", json=body, headers=gate.owner_a.csrf, timeout=90
    ) as response:
        expect_status(response, 200)
        async for line in response.aiter_lines():
            name, _, data = line.partition(":")
            if name != "data":
                continue
            event = json.loads(data)
            if event["type"] in {
                "TEXT_MESSAGE_CHUNK",
                "TEXT_MESSAGE_CONTENT",
            } and event.get("delta"):
                partial = event["delta"]
                break
        require(bool(partial), "creator never saw initial output")
        state = await gate.owner_b.json("GET", f"{base}/snapshot?runId={public}")
        require(
            state["runStatus"] in {"queued", "running"},
            "run already ended before detach",
        )
    # Exiting the creator's response cancels only its network read, and sends no stop command.
    full = checked_sse(await gate.owner_b.send("GET", f"{base}/runs/{public}/stream"))
    _public_events(full, thread, public, "never-publish-gate-test")
    _, output = _answer(full)
    require(
        output.startswith(partial)
        and output == "E2E slow stream completed after detached navigation.",
        "next worker failed to restore the completed answer after upstream detach",
    )
    rows = await gate.sql(
        "SELECT id, status FROM conversation_runs WHERE conversation_id=%s", (thread,)
    )
    require(
        len(rows) == 1 and rows[0]["status"] == "completed",
        "reader started or canceled work",
    )
    return {
        "physical_run": str(rows[0]["id"]),
        "prefix_characters": len(partial),
        "final_characters": len(output),
        "durable_run_count": len(rows),
    }


async def run(selected: list[str]) -> None:
    from http_approval import check_approval
    from http_compatibility import check_compatibility
    from http_termination import check_termination

    cases = {
        "start": check_start_and_recover,
        "detach": check_detach,
        "termination": check_termination,
        "approval": check_approval,
        "compatibility": check_compatibility,
    }
    async with open_gate() as gate:
        results = {}
        failures = []
        path: Path = gate.evidence_dir / "http-contract-results.json"
        for name in selected:
            try:
                results[name] = await cases[name](gate)
            except (
                AssertionError,
                KeyError,
                ValueError,
                httpx.HTTPError,
                psycopg.Error,
            ) as exc:
                # Cases create independent rooms. Preserve other outcomes even if this one
                # fails, keeping arbitrary server/SDK values out of persistent diagnostics.
                failures.append(name)
                results[name] = {
                    "failure": str(exc)
                    if isinstance(exc, GateFailure)
                    else type(exc).__name__,
                    "source": [
                        f"{Path(frame.filename).name}:{frame.lineno}"
                        for frame in traceback.extract_tb(exc.__traceback__)
                        if Path(frame.filename).parent == Path(__file__).parent
                    ],
                }
                print(f"{name} failed", flush=True)
            else:
                print(f"{name} passed", flush=True)
            path.write_text(json.dumps(results, indent=2, default=str) + "\n")
        require(not failures, f"failed HTTP contract cases: {', '.join(failures)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case",
        choices=["start", "detach", "termination", "approval", "compatibility"],
        action="append",
    )
    options = parser.parse_args()
    asyncio.run(
        run(
            options.case
            or ["start", "detach", "termination", "approval", "compatibility"]
        )
    )
