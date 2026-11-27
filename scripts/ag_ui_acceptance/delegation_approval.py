"""Resolve a protected action in the same targeted sibling after fresh authentication."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from http_common import Gate, checked_sse, expect_status, open_gate, require

if TYPE_CHECKING:
    from delegation_parity import ResearchModel


async def check_targeted_approval(gate: Gate, model: ResearchModel, tid: str, rid: str) -> None:
    base = f"/api/conversations/{tid}"
    records: dict[str, Any] = {"threadId": tid, "startRunId": rid}

    def record(**observed: Any) -> None:
        records.update(observed)
        (gate.evidence_dir / "targeted-sibling-approval.json").write_text(
            json.dumps(records, indent=2) + "\n"
        )

    public = checked_sse(await gate.owner_b.send("GET", f"{base}/ag-ui/runs/{rid}/stream"))
    terminal = public[-1].get("outcome", {})
    record(
        phase="original terminal",
        outcome=terminal.get("type"),
        histories=model.histories,
        terminalType=public[-1]["type"],
        failureCode=public[-1].get("code"),
    )
    require(
        terminal.get("type") == "interrupt",
        "targeted child did not ask for protected approval",
    )
    [waiting] = terminal["interrupts"]
    iid, sid = waiting["id"], waiting["subagentRunId"]
    [action] = waiting["metadata"]["moldy"]["action_requests"]
    require(action["name"] == "glob", "approval does not name the protected child action")
    require(
        model.histories == {"alpha": [0, 1], "beta": [0, 0]},
        "wrong child saw the instruction before approval",
    )
    before = await gate.sql(
        "SELECT status,decision_no FROM approval_requests "
        "WHERE interrupt_id=%s AND conversation_id=%s",
        (iid, tid),
    )
    require(
        before == [{"status": "pending", "decision_no": 0}],
        "native approval already consumed before decision",
    )
    record(phase="approval pending", interruptId=iid, subagentRunId=sid)
    await gate.owner_a.client.aclose()
    await gate.owner_b.client.aclose()
    async with open_gate() as fresh:
        require(fresh.owner_b.user_id == gate.owner_b.user_id, "fresh login changed actor")
        snap_url = f"{base}/ag-ui/snapshot?runId={rid}"
        snapshot = await fresh.owner_b.json("GET", snap_url)
        foreign = await fresh.other_b.send("GET", snap_url)
        expect_status(foreign, 404)
        native = await fresh.owner_a.json(
            "GET", f"{base}/langgraph/threads/{tid}/state?read_only=true"
        )
        native_interrupts = [
            i.get("id") for t in native.get("tasks", []) for i in t.get("interrupts", [])
        ]
        task_page = await fresh.owner_a.json("GET", f"{base}/child-tasks")
        tasks = task_page["items"]
        by_call = {t["tool_call_id"]: t for t in tasks}
        alpha, beta = by_call["delegate-alpha"], by_call["delegate-beta"]
        children = {c["childTaskId"]: c for c in snapshot["subagents"] if c.get("childTaskId")}
        a, b = children[alpha["id"]], children[beta["id"]]
        record(
            phase="fresh login",
            targetId=alpha["id"],
            siblingId=beta["id"],
            foreignRead=foreign.status_code,
            nativeInterruptIds=native_interrupts,
            standardInterrupts=[(i["id"], i.get("subagentRunId")) for i in snapshot["interrupts"]],
            childStates={"target": a["status"], "sibling": b["status"]},
            targetInterruptIds=a["interruptIds"],
            siblingInterruptIds=b["interruptIds"],
        )
        require(
            [i["id"] for i in snapshot["interrupts"]] == [iid]
            and snapshot["interrupts"][0].get("subagentRunId") == sid,
            "fresh session lost protected interrupt identity",
        )
        require(iid in native_interrupts, "native reader lost the same pending interrupt")
        require(
            a["subagentRunId"] == sid
            and a["interruptIds"] == [iid]
            and a["status"] == "interrupted"
            and not b["interruptIds"]
            and b["status"] == "completed",
            "sibling assignment changed after reauthentication",
        )
        require(
            model.key not in json.dumps(snapshot),
            "fixture key leaked into standard snapshot",
        )
        (gate.evidence_dir / "targeted-sibling-pending-snapshot.json").write_text(
            json.dumps(snapshot) + "\n"
        )
        require(
            model.key not in json.dumps(task_page),
            "fixture key leaked into child controls",
        )
        (gate.evidence_dir / "targeted-sibling-pending-tasks.json").write_text(
            json.dumps(task_page) + "\n"
        )
        public_resume = str(uuid4())
        body = {
            "threadId": tid,
            "runId": public_resume,
            "protocolVersion": "1.0",
            "messages": [],
            "state": {},
            "tools": [],
            "context": [],
            "forwardedProps": {},
            "resume": [
                {
                    "interruptId": iid,
                    "status": "resolved",
                    "payload": {"decisions": [{"type": "approve"}]},
                }
            ],
        }
        response = await fresh.owner_b.send("POST", f"{base}/ag-ui/runs", body)
        record(
            phase="decision submitted",
            resumeRunId=public_resume,
            responseStatus=response.status_code,
        )
        continued = checked_sse(response)
        terminal = continued[-1]
        record(
            phase="continuation terminal",
            terminalType=terminal["type"],
            outcome=terminal.get("outcome", {}).get("type"),
            failureCode=terminal.get("code"),
            histories=model.histories,
        )
        require(
            terminal["type"] == "RUN_FINISHED"
            and terminal.get("outcome", {}).get("type", "success") == "success",
            "targeted continuation failed",
        )
        require(
            model.histories == {"alpha": [0, 1, 1], "beta": [0, 0]},
            "resume re-executed the sibling or lost the targeted input",
        )
        model_calls = model.requests
        final_page = await fresh.owner_a.json("GET", f"{base}/child-tasks")
        final = final_page["items"]
        by_id = {t["id"]: t for t in final}
        require(
            set(by_id) == {alpha["id"], beta["id"]},
            "approved child was duplicated or replaced",
        )
        for t, source, receipts in ((alpha, "alpha", ["applied"]), (beta, "beta", [])):
            row = by_id[t["id"]]
            require(
                row["status"] == "completed"
                and row["result"] == f"report {source}, addressed instructions {len(receipts)}"
                and [r["status"] for r in row["instructions"]] == receipts,
                "final result/receipt belongs to the wrong child",
            )
        final_snap = await fresh.owner_a.json("GET", f"{base}/ag-ui/snapshot?runId={public_resume}")
        final_native = await fresh.owner_b.json(
            "GET", f"{base}/langgraph/threads/{tid}/state?read_only=true"
        )
        from delegation_parity import message_text

        for source in ("alpha", "beta"):
            expected = by_id[by_call[f"delegate-{source}"]["id"]]["result"]
            require(
                any(
                    m.get("tool_call_id") == f"delegate-{source}"
                    and message_text(m.get("content")) == expected
                    for m in final_native["values"]["messages"]
                ),
                "native history lost targeted result link",
            )
            require(
                any(
                    m.get("role") == "tool" and m.get("content") == expected
                    for m in final_snap["messages"]
                ),
                "standard history lost targeted result",
            )
        require(
            not final_snap["interrupts"] and model.key not in json.dumps(final_snap),
            "finished snapshot exposed stale approval or credential",
        )
        (gate.evidence_dir / "targeted-sibling-final-snapshot.json").write_text(
            json.dumps(final_snap) + "\n"
        )
        require(
            model.key not in json.dumps(final_page),
            "fixture key leaked in final child controls",
        )
        (gate.evidence_dir / "targeted-sibling-final-tasks.json").write_text(
            json.dumps(final_page) + "\n"
        )
        decisions = await fresh.sql(
            "SELECT status, decision_no, decided_by::text AS actor FROM approval_requests "
            "WHERE conversation_id=%s AND interrupt_id=%s",
            (tid, iid),
        )
        require(
            decisions == [{"status": "approved", "decision_no": 1, "actor": fresh.owner_b.user_id}],
            "expected exactly one owned approval decision",
        )
        runs = await fresh.sql(
            "SELECT id::text AS id, parent_run_id::text AS parent, status, source "
            "FROM conversation_runs WHERE conversation_id=%s ORDER BY created_at,id",
            (tid,),
        )
        require(
            len(runs) == 2
            and runs[0]["id"] == rid
            and runs[1]["parent"] == rid
            and runs[1]["status"] == "completed",
            "read or resume created extra runs",
        )
        require(model.requests == model_calls, "read-only restoration invoked the model")
        record(
            phase="passed",
            physicalRuns=runs,
            decisions=decisions,
            modelCalls=model_calls,
            readModelCalls=model.requests - model_calls,
        )
