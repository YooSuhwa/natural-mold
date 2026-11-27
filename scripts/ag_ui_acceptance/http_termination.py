"""Exercise explicit cancellation and a real worker model failure over the public API."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from http_common import Gate, checked_sse, expect_status, require


def _input(thread: str, public: str, marker: str) -> dict[str, Any]:
    return {
        "threadId": thread,
        "runId": public,
        "protocolVersion": "1.0",
        "messages": [{"role": "user", "id": uuid4().hex, "content": marker}],
    }


async def check_termination(gate: Gate) -> dict[str, object]:
    _, tid = await gate.create_chat("stream termination")
    base = f"/api/conversations/{tid}/ag-ui"
    public = f"cancel-{uuid4().hex}"
    sent_cancel = False
    async with gate.owner_a.client.stream(
        "POST",
        f"{base}/runs",
        json=_input(tid, public, "E2E_VISUAL_SLOW_STREAM"),
        headers=gate.owner_a.csrf,
        timeout=90,
    ) as stream:
        expect_status(stream, 200)
        observed = []
        async for line in stream.aiter_lines():
            field, _, data = line.partition(":")
            if field != "data":
                continue
            frame = json.loads(data)
            observed.append(frame)
            if sent_cancel or frame["type"] not in {
                "TEXT_MESSAGE_CONTENT",
                "TEXT_MESSAGE_CHUNK",
            }:
                continue
            snapshot = await gate.owner_b.json("GET", f"{base}/snapshot?runId={public}")
            require(
                snapshot["runStatus"] == "running",
                "worker finished before explicit stop",
            )
            physical = snapshot["physicalRunId"]
            route = f"/api/conversations/{tid}/runs/{physical}/cancel"
            expect_status(await gate.other_b.send("POST", route), 404)
            expect_status(await gate.owner_b.send("POST", route), 200)
            sent_cancel = True
    require(
        sent_cancel and observed[-1]["type"] == "RUN_FINISHED",
        "missing cancel acknowledgement",
    )
    require(
        observed[-1].get("outcome", {}).get("type") == "cancelled",
        "explicit stop appeared as successful completion",
    )
    read = checked_sse(await gate.owner_b.send("GET", f"{base}/runs/{public}/stream"))
    require(
        read[-1].get("outcome", {}).get("type") == "cancelled",
        "restored cancel outcome changed",
    )
    require(
        not "".join(e.get("delta", "") for e in read).endswith(
            "visual stream fixture complete."
        ),
        "worker continued publishing the final scripted answer after a cancellation",
    )

    broken = f"failure-{uuid4().hex}"
    failed = checked_sse(
        await gate.owner_a.send(
            "POST", f"{base}/runs", _input(tid, broken, "E2E_ERROR")
        )
    )
    require(failed[-1]["type"] == "RUN_ERROR", "model failure appeared as a success")
    require(
        not any("E2E scripted model error simulation" in str(e) for e in failed),
        "raw provider exception was disclosed",
    )
    read_failed = checked_sse(
        await gate.owner_b.send("GET", f"{base}/runs/{broken}/stream")
    )
    require(read_failed[-1]["type"] == "RUN_ERROR", "retained error outcome changed")
    states = await gate.sql(
        "SELECT status FROM conversation_runs WHERE conversation_id=%s ORDER BY created_at",
        (tid,),
    )
    require(
        [r["status"] for r in states] == ["canceled", "failed"],
        "stop or replay launched another execution",
    )
    return {
        "cancellation": "cancelled",
        "failure": "RUN_ERROR",
        "durable_runs": len(states),
    }
