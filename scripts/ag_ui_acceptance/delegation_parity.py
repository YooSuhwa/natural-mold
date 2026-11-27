"""Replay one live same-role delegation through the legacy and standard readers."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from aiohttp import web
from aiohttp.test_utils import TestServer
from http_common import Gate, checked_sse, expect_status, open_gate, require

SOURCES = ("alpha", "beta")
FOLLOWUP = "Please include the approved alpha-only footnote."
ROOT_REPLY = "Both local checks have finished."


def message_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    return "".join(
        part if isinstance(part, str) else part["text"]
        for part in value
        if isinstance(part, str)
        or (
            isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
        )
    )


def call(tool: str, ident: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": ident,
        "type": "function",
        "function": {"name": tool, "arguments": json.dumps(args)},
    }


@dataclass
class ResearchModel:
    key: str = field(default_factory=lambda: uuid4().hex)
    role: str = ""
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    arrivals: set[str] = field(default_factory=set)
    histories: dict[str, list[int]] = field(
        default_factory=lambda: {k: [] for k in SOURCES}
    )
    requests: int = 0

    async def respond(self, req: web.Request) -> web.Response:
        if req.headers.get("Authorization") != f"Bearer {self.key}":
            raise web.HTTPUnauthorized
        body = await req.json()
        users = [m.get("content") for m in body["messages"] if m.get("role") == "user"]
        tool_results = [m for m in body["messages"] if m.get("role") == "tool"]
        source = next((s for s in SOURCES if s in users), None)
        self.requests += 1
        message: dict[str, Any] = {"role": "assistant", "content": ""}
        if source is None:
            if tool_results:
                message["content"] = ROOT_REPLY
            else:
                message["tool_calls"] = [
                    call(
                        "task",
                        f"delegate-{s}",
                        {"description": s, "subagent_type": self.role},
                    )
                    for s in SOURCES
                ]
        else:
            received = users.count(FOLLOWUP)
            self.histories[source].append(received)
            if tool_results:
                message["content"] = (
                    f"report {source}, addressed instructions {received}"
                )
            else:
                self.arrivals.add(source)
                if self.arrivals == set(SOURCES):
                    self.ready.set()
                await asyncio.wait_for(self.release.wait(), 60)
                offered = {t["function"]["name"] for t in body["tools"]}
                if "ls" not in offered:
                    raise web.HTTPBadRequest(
                        text="A read-only file listing tool is required"
                    )
                message["tool_calls"] = [call("ls", f"list-{source}", {"path": "/"})]
        finish = "tool_calls" if "tool_calls" in message else "stop"
        envelope = {
            "id": f"chatcmpl-{uuid4().hex}",
            "model": body["model"],
            "created": 1,
        }
        usage = {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}
        if not body.get("stream"):
            return web.json_response(
                {
                    **envelope,
                    "object": "chat.completion",
                    "choices": [
                        {"index": 0, "message": message, "finish_reason": finish}
                    ],
                    "usage": usage,
                }
            )
        if "tool_calls" in message:
            message["tool_calls"] = [
                {"index": i, **c} for i, c in enumerate(message["tool_calls"])
            ]
        rows = [
            {"choices": [{"index": 0, "delta": message, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
            {"choices": [], "usage": usage},
        ]
        frames = [
            json.dumps({**envelope, "object": "chat.completion.chunk", **r})
            for r in rows
        ]
        return web.Response(
            content_type="text/event-stream",
            text="".join(f"data: {value}\n\n" for value in [*frames, "[DONE]"]),
        )


async def run_case(gate: Gate, model: ResearchModel, url: str) -> dict[str, Any]:
    http = gate.owner_a
    name = f"sibling-model-{uuid4().hex[:8]}"
    credential = await http.json(
        "POST",
        "/api/credentials",
        {
            "name": name,
            "definition_key": "openai_compatible",
            "data": {"api_key": model.key, "base_url": url},
        },
        expected=201,
    )
    definition = await http.json(
        "POST",
        "/api/models",
        {
            "provider": "openai_compatible",
            "model_name": name,
            "display_name": name,
            "base_url": url,
            "default_credential_id": credential["id"],
            "source": "manual",
            "supports_function_calling": True,
            "context_window": 32000,
        },
        expected=201,
    )
    child = await http.json(
        "POST",
        "/api/agents",
        {
            "name": name,
            "system_prompt": "Check the assigned source.",
            "model_id": definition["id"],
        },
        expected=201,
    )
    model.role = f"agent_{UUID(child['id']).hex[:8]}"
    aid, thread = await gate.create_chat(
        "same-role delegated checks",
        agent_overrides={
            "model_id": definition["id"],
            "sub_agent_ids": [child["id"]],
        },
    )
    for ident in (child["id"], aid):
        await http.json(
            "PATCH",
            f"/api/agents/{ident}/memory-settings",
            {"memory_policy_override": "off"},
        )
    base = f"/api/conversations/{thread}"
    legacy = f"{base}/langgraph/threads/{thread}"
    request = str(uuid4())
    command = await http.json(
        "POST",
        f"{legacy}/commands",
        {
            "id": request,
            "method": "run.start",
            "params": {
                "client_request_id": request,
                "input": {
                    "messages": [
                        {"role": "user", "content": "Check both local sources."}
                    ]
                },
            },
        },
    )
    rid = str(UUID(command["result"]["run_id"]))
    async with asyncio.timeout(60):
        while not model.ready.is_set():
            try:
                await asyncio.wait_for(model.ready.wait(), 1)
            except TimeoutError:
                status = await gate.sql(
                    "SELECT status, error_code FROM conversation_runs WHERE id = %s",
                    (rid,),
                )
                require(
                    status[0]["status"] in {"accepted", "queued", "running"},
                    f"parent ended before sibling dispatch: {status}, provider calls={model.requests}",
                )
    controls = f"{base}/child-tasks"
    tasks = (await gate.owner_b.json("GET", controls))["items"]
    by_call = {t["tool_call_id"]: t for t in tasks}
    require(
        set(by_call) == {"delegate-alpha", "delegate-beta"},
        "two actual delegated tasks required",
    )
    require(
        {t["child_agent_id"] for t in tasks} == {child["id"]},
        "expected the same child agent",
    )
    destination = by_call["delegate-alpha"]["id"]
    payload = {"client_request_id": str(uuid4()), "text": FOLLOWUP}
    path = f"{controls}/{destination}/instructions"
    try:
        for actor in (http, gate.owner_b):
            accepted = await actor.json("POST", path, payload)
            require(len(accepted["instructions"]) == 1, "duplicate target instruction")
        expect_status(await gate.other_b.send("GET", controls), 404)
    finally:
        model.release.set()
    public = checked_sse(
        await gate.owner_b.send("GET", f"{base}/ag-ui/runs/{rid}/stream")
    )
    require(public[-1]["type"] == "RUN_FINISHED", "siblings failed to finish")
    require(
        model.histories == {"alpha": [0, 1], "beta": [0, 0]},
        "misdirected model instruction",
    )
    model_calls = model.requests
    old = await http.json("GET", f"{legacy}/state?read_only=true")
    snapshot = await gate.owner_b.json("GET", f"{base}/ag-ui/snapshot?runId={rid}")
    saved = (await http.json("GET", controls))["items"]
    starts = [e for e in public if e["type"] == "SUBAGENT_STARTED"]
    ends = [e["subagentRunId"] for e in public if e["type"] == "SUBAGENT_FINISHED"]
    require(
        len(starts) == len(ends) == len(set(ends)) == 2
        and {e["subagentRunId"] for e in starts} == set(ends),
        "incomplete sibling lifecycle",
    )
    require(
        len({e["parentToolCallId"] for e in starts}) == 2,
        "siblings shared a parent call",
    )
    native_messages = old["values"]["messages"]

    def summarize(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "role": m.get("role") or m.get("type"),
                "content_shape": type(m.get("content")).__name__,
                "fixture_terms": [
                    label
                    for label in (ROOT_REPLY, "report alpha", "report beta")
                    if label in message_text(m.get("content"))
                ],
                "has_tool_id": bool(m.get("tool_call_id") or m.get("toolCallId")),
            }
            for m in messages
        ]

    observation = {
        "thread": thread,
        "run": rid,
        "model_instructions_per_call": model.histories,
        "terminal": public[-1]["type"],
        "model_calls": model_calls,
        "native": summarize(native_messages),
        "standard": summarize(snapshot["messages"]),
        "child_summaries": [
            {
                "task": t["id"],
                "calls": t["tool_call_id"],
                "status": t["status"],
                "receipt_states": [r["status"] for r in t["instructions"]],
                "result_matches": [
                    s for s in SOURCES if f"report {s}" in (t["result"] or "")
                ],
            }
            for t in saved
        ],
        "snapshot_children": [
            {
                "status": c["status"],
                "has_task_id": bool(c.get("childTaskId")),
                "has_parent_call": bool(c.get("parentToolCallId")),
            }
            for c in snapshot["subagents"]
        ],
        "public_subagent_starts": len(starts),
        "public_subagent_finishes": len(ends),
    }
    (gate.evidence_dir / "same-role-observations.json").write_text(
        json.dumps(observation, indent=2) + "\n"
    )
    require(
        any(message_text(m.get("content")) == ROOT_REPLY for m in native_messages),
        "native root reply missing",
    )
    require(
        any(m.get("content") == ROOT_REPLY for m in snapshot["messages"]),
        "standard root reply missing",
    )
    snapshot_children = {s["childTaskId"]: s for s in snapshot["subagents"]}
    scope_by_message = {
        m["messageId"]: m.get("subagentRunId") for m in snapshot["messageLinks"]
    }
    public_by_call = {e["parentToolCallId"]: e["subagentRunId"] for e in starts}
    consumed: set[str] = set()
    for task in saved:
        raw_call = task["tool_call_id"]
        source = raw_call.removeprefix("delegate-")
        result = f"report {source}, addressed instructions {int(source == 'alpha')}"
        receipts = task["instructions"]
        require(
            [r["status"] for r in receipts]
            == (["applied"] if source == "alpha" else []),
            "wrong child receipt",
        )
        require(
            task["status"] == "completed" and task["result"] == result,
            "control/result binding changed",
        )
        require(
            any(
                m.get("tool_call_id") == raw_call
                and message_text(m.get("content")) == result
                for m in native_messages
            ),
            "v3 result attached to the wrong child",
        )
        restored = snapshot_children[task["id"]]
        scoped = restored["subagentRunId"]
        require(
            public_by_call[restored["parentToolCallId"]] == scoped,
            "snapshot child identity differs from stream",
        )
        snapshot_text = {
            m.get("content")
            for m in snapshot["messages"]
            if m["role"] == "assistant" and scope_by_message.get(m["id"]) == scoped
        }
        require(
            restored.get("result") in {None, result}
            and (result in snapshot_text or restored.get("result") == result),
            "snapshot attached the wrong child result",
        )
        child_text = "".join(
            e.get("delta", "")
            for e in public
            if e.get("subagentRunId") == scoped
            and e["type"] in {"TEXT_MESSAGE_CHUNK", "TEXT_MESSAGE_CONTENT"}
        )
        require(
            result in child_text
            and all(
                f"report {other}," not in child_text
                for other in SOURCES
                if other != source
            ),
            "child output was attributed to another invocation",
        )
        consumed.add(scoped)
    require(consumed == set(ends), "missing scoped child result on reattach")
    expect_status(
        await gate.other_b.send("GET", f"{base}/ag-ui/snapshot?runId={rid}"), 404
    )
    runs = await gate.sql(
        "SELECT id FROM conversation_runs WHERE conversation_id = %s", (thread,)
    )
    require(len(runs) == 1 and str(runs[0]["id"]) == rid, "reads created a new run")
    require(model.requests == model_calls, "reads caused a model request")
    return {
        "thread": thread,
        "run": rid,
        "same_role_children": len(consumed),
        "model_instructions_per_call": model.histories,
        "additional_model_calls": model.requests - model_calls,
        "physical_runs": len(runs),
        "events": dict(Counter(e["type"] for e in public)),
    }


async def main() -> None:
    model = ResearchModel()
    app = web.Application()
    app.router.add_post("/v1/chat/completions", model.respond)
    async with open_gate() as gate, TestServer(app, host="127.0.0.1") as server:
        try:
            result = await run_case(gate, model, str(server.make_url("/v1")))
            (gate.evidence_dir / "same-role-delegation.json").write_text(
                json.dumps(result, indent=2) + "\n"
            )
        finally:
            model.release.set()


if __name__ == "__main__":
    asyncio.run(main())
