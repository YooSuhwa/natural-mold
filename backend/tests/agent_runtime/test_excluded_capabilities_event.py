"""F-06 / plan 13.6 — ``moldy.excluded_capabilities`` run-start event (v3 + legacy SSE)."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph
from app.agent_runtime.run_start_events import (
    excluded_capabilities_of,
    excluded_capabilities_payload,
)
from app.agent_runtime.streaming import stream_agent_response
from app.authz.runtime_access_planner import Exclusion
from tests.agent_runtime.langgraph_streaming_fixtures import ProtocolAgent, sse_payload

TOOL_ID = uuid.uuid4()
SKILL_ID = uuid.uuid4()
EXCLUDED = [
    Exclusion("tool", TOOL_ID, "denied").as_dict(),
    Exclusion("tool", uuid.uuid4(), "denied").as_dict(),
    Exclusion("skill", SKILL_ID, "broken").as_dict(),
]
EXPECTED = {
    "items": [
        {"type": "skill", "reason": "broken", "count": 1},
        {"type": "tool", "reason": "denied", "count": 2},
    ],
    "total": 3,
}


def _raw_message() -> dict[str, Any]:
    return {
        "type": "event",
        "method": "messages",
        "params": {"namespace": [], "data": {"chunk": "hi"}},
        "seq": 1,
        "event_id": "upstream-1",
    }


async def _v3(excluded: list[dict[str, str]] | None, trace: list[dict[str, Any]]) -> list[Any]:
    chunks = [
        chunk
        async for chunk in stream_agent_response_langgraph(
            ProtocolAgent([_raw_message()]),
            {"messages": []},
            {"configurable": {"thread_id": "thread-excluded"}},
            run_id="run-excluded",
            trace_sink=trace,
            excluded_capabilities=excluded,
        )
    ]
    return [sse_payload(chunk) for chunk in chunks]


class _Agent:
    async def astream(self, *_args: Any, **_kwargs: Any) -> Any:
        if False:  # pragma: no cover - empty async generator
            yield None


async def _legacy(excluded: list[dict[str, str]] | None) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    async for raw in stream_agent_response(_Agent(), [], {}, excluded_capabilities=excluded):
        lines = raw.strip().splitlines()
        name = next(line for line in lines if line.startswith("event: ")).removeprefix("event: ")
        data = next(line for line in lines if line.startswith("data: ")).removeprefix("data: ")
        out.append((name, json.loads(data)))
    return out


def _assert_no_ids(value: Any) -> None:
    text = json.dumps(value)
    assert str(TOOL_ID) not in text and str(SKILL_ID) not in text
    assert '"id"' not in text and "resource_id" not in text


@pytest.mark.asyncio
async def test_v3_emits_excluded_capabilities_once_at_head_with_stable_id() -> None:
    trace: list[dict[str, Any]] = []
    payloads = await _v3(EXCLUDED, trace)

    assert [p["method"] for p in payloads] == ["lifecycle", "custom", "messages", "lifecycle"]
    event = payloads[1]
    assert event["params"]["data"]["name"] == "moldy.excluded_capabilities"
    assert event["params"]["data"]["payload"] == EXPECTED
    # Stable id → reload/reconnect replay dedupes (subagent_names contract).
    assert event["event_id"] == "run-excluded:excluded_capabilities"
    persisted = [e for e in trace if "excluded_capabilities" in json.dumps(e)]
    assert len(persisted) == 1
    assert persisted[0]["id"] == "run-excluded:excluded_capabilities"
    assert persisted[0]["data"]["payload"] == EXPECTED
    _assert_no_ids(event["params"]["data"])
    _assert_no_ids(persisted[0]["data"])


@pytest.mark.asyncio
@pytest.mark.parametrize("excluded", [None, []])
async def test_v3_omits_event_when_nothing_excluded(excluded: list[dict[str, str]] | None) -> None:
    payloads = await _v3(excluded, [])
    assert [p["method"] for p in payloads] == ["lifecycle", "messages", "lifecycle"]


@pytest.mark.asyncio
async def test_legacy_sse_emits_event_right_after_message_start() -> None:
    events = await _legacy(EXCLUDED)
    assert events[0][0] == "message_start"
    assert events[1] == ("moldy.excluded_capabilities", EXPECTED)
    assert [name for name, _ in events].count("moldy.excluded_capabilities") == 1
    _assert_no_ids(events[1][1])


@pytest.mark.asyncio
@pytest.mark.parametrize("excluded", [None, []])
async def test_legacy_sse_omits_event_when_nothing_excluded(
    excluded: list[dict[str, str]] | None,
) -> None:
    names = [name for name, _ in await _legacy(excluded)]
    assert "moldy.excluded_capabilities" not in names


def test_payload_rebuilds_items_from_type_and_reason_only() -> None:
    leaky = [
        {"type": "mcp_tool", "reason": "denied", "id": str(TOOL_ID), "resource_id": str(SKILL_ID)},
        {"type": "tool", "reason": "unknown"},
        {"reason": "denied"},
    ]
    payload = excluded_capabilities_payload(leaky)
    assert payload == {"items": [{"type": "mcp_tool", "reason": "denied", "count": 1}], "total": 1}
    _assert_no_ids(payload)
    assert excluded_capabilities_payload([{"type": "tool", "reason": "other"}]) is None


def test_excluded_capabilities_of_reads_the_plan() -> None:
    plan = SimpleNamespace(excluded_capabilities=EXCLUDED)
    assert excluded_capabilities_of(SimpleNamespace(access_plan=plan)) == EXCLUDED
    assert excluded_capabilities_of(SimpleNamespace(access_plan=None)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("runner_module", "entry", "stream_name"),
    [
        (
            "langgraph_agent_stream_runner",
            "execute_agent_stream_langgraph",
            "stream_agent_response_langgraph",
        ),
        ("agent_stream_runner", "execute_agent_stream", "stream_agent_response"),
    ],
)
async def test_runners_pass_the_plan_exclusions_to_the_stream(
    monkeypatch: pytest.MonkeyPatch, runner_module: str, entry: str, stream_name: str
) -> None:
    import importlib

    from app.agent_runtime.runtime_config import AgentConfig
    from tests.authz.unit_scope_fixture import unit_context
    from tests.conftest import TEST_USER_ID

    module = importlib.import_module(f"app.agent_runtime.{runner_module}")
    captured: dict[str, Any] = {}

    async def fake_prepare_agent(_cfg: Any, **_kwargs: Any) -> Any:
        return "agent", ["lc-message"], {"configurable": {"thread_id": "thread-runner"}}

    async def fake_stream(_agent: Any, _input: Any, _config: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        yield "chunk"

    monkeypatch.setattr(module, "_prepare_agent", fake_prepare_agent)
    monkeypatch.setattr(module, stream_name, fake_stream)
    cfg = AgentConfig(
        org_context=unit_context(TEST_USER_ID),
        provider="fake",
        model_name="fake-chat",
        api_key=None,
        base_url=None,
        system_prompt="s",
        tools_config=[],
        thread_id="thread-runner",
    )
    cfg.access_plan = SimpleNamespace(excluded_capabilities=EXCLUDED)  # type: ignore[assignment]

    chunks = [c async for c in getattr(module, entry)(cfg, [], run_id="run-1")]

    assert chunks == ["chunk"]
    assert captured["excluded_capabilities"] == EXCLUDED
