"""Compare captured Moldy approvals to a locally executed official LangGraph adapter.

Use a test environment with ag-ui-langgraph[fastapi]==0.0.48, ag-ui-protocol==1.0.0
and the application's locked LangGraph. The reference graph makes no network calls.
Each argument is a JSON file containing a public HTTP request and its observed events.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
from typing import Any

import httpx
from ag_ui.core import (
    Event,
    RunAgentInput,
    RunFinishedEvent,
    RunFinishedInterruptOutcome,
    RunStartedEvent,
)
from fastapi import FastAPI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, TypeAdapter


class Decision(BaseModel):
    approved: bool


class ApprovalState(MessagesState):
    approved: bool


def ask_for_approval(state: ApprovalState) -> dict[str, bool]:
    response = interrupt(
        {
            "prompt": "Approve this local fixture?",
            "message_count": len(state["messages"]),
        },
        response_schema=Decision,
    )
    return {"approved": response.approved}


@dataclass(frozen=True)
class Capture:
    request: RunAgentInput
    finish: RunFinishedEvent

    @classmethod
    def parse(cls, request: dict[str, Any], frames: list[dict[str, Any]]) -> Capture:
        command = RunAgentInput.model_validate(request)
        if not frames:
            raise ValueError("expected a non-empty approval stream")
        events = TypeAdapter(list[Event]).validate_python(frames)
        first, last = events[0], events[-1]
        if not isinstance(first, RunStartedEvent) or not isinstance(last, RunFinishedEvent):
            raise TypeError("approval capture must start and finish the submitted run")
        for event in (first, last):
            if (event.thread_id, event.run_id) != (command.thread_id, command.run_id):
                raise ValueError("run events did not match the submitted input")
        return cls(command, last)

    @classmethod
    def load(cls, path: Path) -> Capture:
        captured = json.loads(path.read_text())
        return cls.parse(captured["request"], captured["events"])


@dataclass(frozen=True)
class Signature:
    interrupt_count: int
    schema_count: int
    resolved_ids_match: bool
    same_thread_new_run: bool
    continuation_outcome: str

    @classmethod
    def of(cls, original: Capture, continuation: Capture) -> Signature:
        asked = original.finish.outcome
        if not isinstance(asked, RunFinishedInterruptOutcome):
            raise TypeError("the interrupted run finished without the standard interrupt outcome")
        ids = [entry.id for entry in asked.interrupts]
        answers = continuation.request.resume or []
        result = continuation.finish.outcome
        return cls(
            interrupt_count=len(ids),
            schema_count=sum(
                isinstance(entry.response_schema, dict) and bool(entry.response_schema)
                for entry in asked.interrupts
            ),
            resolved_ids_match=(
                len(ids) == len(set(ids)) == len(answers)
                and sorted(ids) == sorted(answer.interrupt_id for answer in answers)
                and all(answer.status == "resolved" for answer in answers)
            ),
            same_thread_new_run=(
                original.request.thread_id == continuation.request.thread_id
                and original.request.run_id != continuation.request.run_id
            ),
            continuation_outcome=result.type if result else "success",
        )


async def reference_signature() -> Signature:
    # The separate reference environment provides this adapter at the version checked by compare().
    adapter = import_module("ag_ui_langgraph")
    builder = StateGraph(ApprovalState).add_node("approval", ask_for_approval)
    builder.add_edge(START, "approval")
    graph = builder.compile(checkpointer=InMemorySaver())
    app = FastAPI()
    adapter.add_langgraph_fastapi_endpoint(
        app,
        adapter.LangGraphAgent(
            name="reference",
            graph=graph,
            emit_raw_events=False,
            emit_interrupt_outcome=True,
            enable_legacy_on_interrupt_event=False,
        ),
        path="/reference",
    )
    request = {
        "protocolVersion": "1.0",
        "threadId": "reference-thread",
        "runId": "first",
        "messages": [{"role": "user", "id": "reference-msg", "content": "verify"}],
        "state": {},
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as http:

        async def submit(body: dict[str, Any]) -> Capture:
            response = await http.post(
                "/reference", json=body, headers={"Accept": "text/event-stream"}
            )
            response.raise_for_status()
            frames = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            return Capture.parse(body, frames)

        first = await submit(request)
        outcome = first.finish.outcome
        if not isinstance(outcome, RunFinishedInterruptOutcome) or len(outcome.interrupts) != 1:
            raise ValueError("official adapter failed to produce the reference interrupt")
        second = await submit(
            {
                **request,
                "runId": "continuation",
                "resume": [
                    {
                        "interruptId": outcome.interrupts[0].id,
                        "status": "resolved",
                        "payload": {"approved": True},
                    }
                ],
            }
        )
        return Signature.of(first, second)


async def compare(first: Path, second: Path) -> None:
    versions = {name: version(name) for name in ("ag-ui-protocol", "ag-ui-langgraph", "langgraph")}
    if versions != {
        "ag-ui-protocol": "1.0.0",
        "ag-ui-langgraph": "0.0.48",
        "langgraph": "1.2.24",
    }:
        raise ValueError(f"unexpected reference environment: {versions}")
    reference = await reference_signature()
    expected = Signature(1, 1, True, True, "success")
    if reference != expected:
        raise ValueError(f"reference approval failed its own contract: {reference}")
    product = Signature.of(Capture.load(first), Capture.load(second))
    if product != reference:
        raise ValueError(f"Moldy approval and official adapter differ: {product} != {reference}")
    print(
        json.dumps(
            {
                "versions": versions,
                "reference": asdict(reference),
                "observed": asdict(product),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("interrupt_capture", type=Path)
    parser.add_argument("continuation_capture", type=Path)
    args = parser.parse_args()
    asyncio.run(compare(args.interrupt_capture, args.continuation_capture))
