"""Exercise real LangGraph interrupts and count the resulting document-skill executions in PG."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
from typing import Any
from uuid import UUID, uuid4
from xml.etree import ElementTree
from zipfile import ZipFile

import psycopg
from http_common import Gate, expect_status, require
from skill_attempts import docx_attempts


async def _docx_skill(gate: Gate) -> str:
    items = await gate.owner_a.json(
        "GET",
        "/api/marketplace/items?resource_type=skill&source_kind=system_seed&limit=200",
    )
    matches = [item for item in items if item["slug"] == "docx-document"]
    require(len(matches) == 1, "expected the seeded docx-document skill")
    item = matches[0]
    installed = item["installation"].get("installed_resource_id")
    if not installed:
        result = await gate.owner_a.json(
            "POST",
            f"/api/marketplace/items/{item['id']}/install",
            {"install_mode": "overwrite_existing"},
            expected=201,
        )
        installed = result["installed_skill_id"]
    return str(UUID(installed))


def _request(thread_id: str, **fields: Any) -> dict[str, Any]:
    return {
        "threadId": thread_id,
        "runId": str(uuid4()),
        "protocolVersion": "1.0",
        "messages": [],
        "state": {},
        "tools": [],
        "context": [],
        "forwardedProps": {},
        **fields,
    }


def _response(
    gate: Gate,
    label: str,
    request: dict[str, Any],
    events: list[dict[str, Any]],
    outcome: str,
) -> dict[str, Any]:
    start, end = events[0], events[-1]
    require(
        start["type"] == "RUN_STARTED" and end["type"] == "RUN_FINISHED",
        "bad run boundaries",
    )
    if request.get("resume"):
        require(
            start.get("parentRunId") is None,
            "a top-level resume was reported as an agent-spawned child",
        )
    for event in (start, end):
        require(
            (event["threadId"], event["runId"])
            == (request["threadId"], request["runId"]),
            "approval response IDs differ from the submitted input",
        )
    terminal = end.get("outcome") or {"type": "success"}
    require(terminal["type"] == outcome, f"{label} expected a terminal {outcome}")
    receipt = {
        "name": label,
        "request": request,
        "events": events,
        "expect": {
            "runId": request["runId"],
            "threadId": request["threadId"],
            "terminal": outcome,
        },
    }
    (gate.evidence_dir / f"ag-ui-approval-{label}.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    )
    return terminal


async def _counts(gate: Gate, thread_id: str, skill_id: str) -> dict[str, Any]:
    # Chat skill usage is an append-only row per successful subprocess (no call-id deduplication).
    # Count that independent witness as well as artifact versions and physical run lineage.
    (counts,) = await gate.sql(
        """
        WITH scope AS (SELECT %s::uuid AS thread, %s::uuid AS skill)
        SELECT
            (SELECT count(*) FROM conversation_runs r, scope s
             WHERE r.conversation_id = s.thread) AS runs,
            (SELECT count(*) FROM conversation_runs r, scope s
             JOIN conversation_runs parent ON parent.conversation_id = s.thread
             WHERE r.conversation_id = s.thread AND r.parent_run_id = parent.id
             AND r.source = 'resume' AND r.status = 'completed') AS resumed,
            (SELECT count(*) FROM approval_requests a, scope s
             WHERE a.conversation_id = s.thread AND a.status = 'pending') AS pending,
            (SELECT count(*) FROM approval_requests a, scope s
             JOIN conversation_runs r ON r.conversation_id = s.thread
             WHERE a.conversation_id = s.thread AND a.resumed_run_id = r.id
             AND a.run_id = r.parent_run_id AND a.status != 'pending') AS claimed,
            (SELECT coalesce(sum(u.execution_count), 0) FROM skill_usage_events u, scope s
             WHERE u.skill_id = s.skill AND u.conversation_id = s.thread
             AND u.source_kind = 'chat_execution') AS executions,
            (SELECT count(*) FROM conversation_artifacts a
             JOIN artifact_versions v ON v.artifact_id = a.id, scope s
             WHERE a.conversation_id = s.thread AND a.source_tool_name = 'execute_in_skill')
               AS versions
    """,
        (thread_id, skill_id),
    )
    return counts


async def _file(gate: Gate, thread_id: str, name: str) -> dict[str, str]:
    artifacts_path = f"/api/conversations/{thread_id}/artifacts"
    artifacts = await gate.owner_b.json("GET", artifacts_path)
    require(
        len(artifacts) == 1 and artifacts[0]["display_name"] == name,
        "wrong number or name of generated files",
    )
    item = artifacts[0]
    path = f"{artifacts_path}/{item['id']}/download"
    data = await gate.owner_b.send("GET", path)
    expect_status(data, 200)
    require(
        hashlib.sha256(data.content).hexdigest() == item["sha256"], "file/hash mismatch"
    )
    with ZipFile(io.BytesIO(data.content)) as archive:
        doc = ElementTree.fromstring(archive.read("word/document.xml"))
        require(
            bool("".join(doc.itertext()).strip()),
            "protected subprocess produced an empty DOCX",
        )
    expect_status(await gate.other_b.send("GET", path), 404)
    return {
        "artifact_id": item["id"],
        "version_id": item["version_id"],
        "sha256": item["sha256"],
    }


async def _check_public_actions(
    gate: Gate, thread_id: str, interrupt: dict[str, Any], size: int
) -> None:
    rows = await gate.sql(
        "SELECT actions FROM approval_requests WHERE conversation_id=%s "
        "AND interrupt_id=%s AND status='pending'",
        (thread_id, interrupt["id"]),
    )
    require(len(rows) == 1, "public interrupt does not identify one pending approval")
    durable = rows[0]["actions"]
    require(interrupt.get("reason") == "moldy.tool_approval", "wrong interrupt kind")
    metadata = interrupt["metadata"]["moldy"]
    actions, reviews = metadata["action_requests"], metadata["review_configs"]
    schema = interrupt["responseSchema"]["properties"]["decisions"]
    require(
        len(durable)
        == len(actions)
        == len(reviews)
        == schema["minItems"]
        == schema["maxItems"]
        == size
        and schema["items"] is False,
        "public actions or decision schema have incorrect batch width",
    )
    for row, action, config, variant in zip(
        durable, actions, reviews, schema["prefixItems"], strict=True
    ):
        choices = variant.get("oneOf", [variant])
        require(
            row["name"] == action["name"] == config["action_name"] == "execute_in_skill"
            and row["args"] == action["args"]
            and set(row["allowed_decisions"])
            == set(config["allowed_decisions"])
            == {entry["properties"]["type"]["const"] for entry in choices},
            "tool order, synthetic file arguments, or allowed decisions differed",
        )


async def _check_claim(
    gate: Gate, thread_id: str, interrupt_id: str, decisions: list[dict[str, str]]
) -> None:
    rows = await gate.sql(
        """SELECT a.status, a.decision_no, a.decisions, a.decided_by::text AS actor
           FROM approval_requests a JOIN conversation_runs r ON r.id = a.resumed_run_id
           WHERE a.conversation_id=%s AND a.interrupt_id=%s
             AND r.parent_run_id=a.run_id AND r.conversation_id=a.conversation_id""",
        (thread_id, interrupt_id),
    )
    types = [d["type"] for d in decisions]
    status = "rejected" if all(t == "reject" for t in types) else "approved"
    require(
        rows
        == [
            {
                "status": status,
                "decision_no": 1,
                "decisions": types,
                "actor": gate.owner_a.user_id,
            }
        ],
        "claimed approval did not preserve the actor, ordered decisions, and physical run linkage",
    )


async def _expire_pending(gate: Gate, thread_id: str, interrupt_id: str) -> None:
    # Leave a quiet window for the independent 60s expiry sweep, which legitimately
    # auto-rejects overdue approvals. This case exercises the HTTP handler on a pending row.
    async with asyncio.timeout(25):
        while True:
            jobs = await gate.sql(
                "SELECT next_run_time - EXTRACT(EPOCH FROM clock_timestamp()) AS seconds "
                "FROM apscheduler_jobs WHERE id='approval_sweep'"
            )
            if not jobs or jobs[0]["seconds"] is None or jobs[0]["seconds"] > 15:
                break
            await asyncio.sleep(0.3)

    # Gate.dsn is the explicitly verified local throwaway DB and its unprivileged RLS role.
    # Change only the clock on the fresh approval whose public interrupt we just validated.
    async with await psycopg.AsyncConnection.connect(gate.dsn) as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', %s, true)", (gate.tenant_id,)
        )
        cursor = await conn.execute(
            """UPDATE approval_requests AS a SET expires_at = clock_timestamp() - interval '1 second'
               FROM conversation_runs AS r WHERE a.conversation_id=%s AND a.interrupt_id=%s
                 AND a.tenant_id=%s AND a.subject_user_id=%s AND a.decider_user_id=%s
                 AND a.kind='tool' AND a.status='pending' AND a.decision_no=0
                 AND a.expires_at>clock_timestamp() AND a.resumed_run_id IS NULL
                 AND r.id=a.run_id AND r.conversation_id=a.conversation_id
                 AND r.tenant_id=a.tenant_id AND r.org_id IS NOT DISTINCT FROM a.org_id
                 AND r.interrupt_id=a.interrupt_id AND r.status='interrupted'
               RETURNING a.id""",
            (
                thread_id,
                interrupt_id,
                gate.tenant_id,
                gate.owner_a.user_id,
                gate.owner_a.user_id,
            ),
        )
        require(
            len(await cursor.fetchall()) == 1,
            "expected one currently pending test approval",
        )


async def _check_decisions(gate: Gate, skill_id: str, kind: str) -> dict[str, object]:
    """Approve competitively, or reject one action out of two after rejecting an incomplete batch."""
    mixed = kind == "mixed"
    _, thread_id = await gate.create_chat(
        f"approval-{kind}", agent_overrides={"skill_ids": [skill_id]}
    )
    route = f"/api/conversations/{thread_id}/ag-ui/runs"
    marker = "E2E_HITL_MULTI" if mixed else "E2E_HITL_APPROVAL"
    command = _request(
        thread_id, messages=[{"id": str(uuid4()), "role": "user", "content": marker}]
    )
    opened = gate.check_sse(await gate.owner_a.send("POST", route, command))
    terminal = _response(gate, f"{kind}-interrupt", command, opened, "interrupt")
    (interruption,) = terminal["interrupts"]
    real_id = interruption["id"]
    await _check_public_actions(gate, thread_id, interruption, 2 if mixed else 1)
    approvals = [{"type": "approve"}]
    reject = {"type": "reject", "message": "decline this document"}
    decisions = {
        "single": approvals,
        "mixed": [*approvals, reject],
        "rejected": [reject],
        "expired": approvals,
    }[kind]

    def continuation(
        answers: list[dict[str, str]], interrupt_id: str = real_id
    ) -> dict[str, Any]:
        return _request(
            thread_id,
            resume=[
                {
                    "interruptId": interrupt_id,
                    "status": "resolved",
                    "payload": {"decisions": answers},
                }
            ],
        )

    expected_before = {
        "runs": 1,
        "resumed": 0,
        "pending": 1,
        "claimed": 0,
        "executions": 0,
        "versions": 0,
    }
    require(
        await _counts(gate, thread_id, skill_id) == expected_before
        and docx_attempts(thread_id) == 0,
        "tool ran before approval",
    )
    if kind == "expired":
        await _expire_pending(gate, thread_id, real_id)
        denied = await gate.owner_b.send("POST", route, continuation(decisions))
        expect_status(denied, 409)
        after = await _counts(gate, thread_id, skill_id)
        attempts = docx_attempts(thread_id)
        require(
            after == expected_before and attempts == 0,
            "expired approval caused a side effect",
        )
        return {
            "thread_id": thread_id,
            "resume_status": denied.status_code,
            "before": expected_before,
            "after": after,
            "process_starts": attempts,
        }
    expect_status(await gate.other_b.send("POST", route, continuation(decisions)), 404)
    expect_status(
        await gate.owner_b.send("POST", route, continuation(decisions, str(uuid4()))),
        409,
    )
    invalid = approvals if mixed else [{"type": "nonexistent"}]
    expect_status(await gate.owner_b.send("POST", route, continuation(invalid)), 422)
    require(
        await _counts(gate, thread_id, skill_id) == expected_before
        and docx_attempts(thread_id) == 0,
        "denied or malformed decision changed a run, approval, or file",
    )

    contenders = [continuation(decisions), continuation(decisions)]
    results = await asyncio.gather(
        gate.owner_a.send("POST", route, contenders[0]),
        gate.owner_b.send("POST", route, contenders[1]),
    )
    statuses = [result.status_code for result in results]
    require(
        sorted(statuses) == [200, 409],
        f"two tabs claiming one approval returned {statuses}",
    )
    winner = statuses.index(200)
    accepted = gate.check_sse(results[winner])
    _response(gate, f"{kind}-continuation", contenders[winner], accepted, "success")

    # Reusing the same accepted request is a read of the original run. A new request aimed at
    # the consumed slot must be rejected. Neither may create another script execution.
    replay = gate.check_sse(await gate.owner_b.send("POST", route, contenders[winner]))
    require(
        replay[-1]["runId"] == accepted[-1]["runId"]
        and replay[-1]["type"] == "RUN_FINISHED",
        "repeat submit did not attach to accepted run",
    )
    expect_status(await gate.owner_a.send("POST", route, continuation(decisions)), 409)
    expected_files = int(kind != "rejected")
    expected_after = {
        "runs": 2,
        "resumed": 1,
        "pending": 0,
        "claimed": 1,
        "executions": expected_files,
        "versions": expected_files,
    }
    counts = await _counts(gate, thread_id, skill_id)
    attempts = docx_attempts(thread_id)
    require(
        counts == expected_after, f"wrong protected effects or resume lineage: {counts}"
    )
    require(
        attempts == expected_files,
        f"expected {expected_files} DOCX process starts, got {attempts}",
    )
    await _check_claim(gate, thread_id, real_id, decisions)
    name = "moldy-hitl-multi-1.docx" if mixed else "moldy-docx-demo.docx"
    artifact = None
    if expected_files:
        artifact = await _file(gate, thread_id, name)
    else:
        artifacts = await gate.owner_b.json(
            "GET", f"/api/conversations/{thread_id}/artifacts"
        )
        require(artifacts == [], "declined action exposed a generated file")
    return {
        "thread_id": thread_id,
        "winner_statuses": statuses,
        "counts": counts,
        "process_starts": attempts,
        "file": artifact,
    }


async def check_approval(gate: Gate) -> dict[str, object]:
    skill = await _docx_skill(gate)
    return {
        kind: await _check_decisions(gate, skill, kind)
        for kind in ("single", "mixed", "rejected", "expired")
    }
