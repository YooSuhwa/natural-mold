"""G-05: the native ``auth_required`` interrupt survives every non-protocol wire.

The v3 protocol stream stores the raw LangGraph value, but the legacy SSE ``interrupt``
event, the reconnect synthesis (``pending_input_requested_events``) and the state API's
checkpoint fallback all go through ``_interrupt_to_standard_chunk``, which used to drop
it — a reload or reconnect lost the card and the REST resume check saw nothing pending.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from langgraph.types import Interrupt

from app.agent_runtime.langgraph_pending_inputs import (
    _interrupt_payloads_from_pending_writes,
    pending_input_requested_events,
)
from app.agent_runtime.streaming import _interrupt_to_standard_chunk, stream_agent_response
from app.config import settings
from app.routers.conversation_resume_decisions import decision_problem
from app.routers.credential_oauth import _oauth_callback_html

_AUTH_VALUE: dict[str, Any] = {
    "type": "auth_required",
    "reason": "expired",
    "items": [
        {
            "resource_type": "mcp_server",
            "resource_name": "Slack",
            "tool_names": ["post_message"],
            "credential_id": "c1",
            "fixable_by": "me",
            "connect": {"kind": "oauth"},
        }
    ],
    "allowed_decisions": ["respond"],
    "attempt": 2,
}


def test_chunk_keeps_the_auth_kind_and_attempt() -> None:
    chunk = _interrupt_to_standard_chunk("intr-auth", dict(_AUTH_VALUE))

    assert chunk == {"interrupt_id": "intr-auth", **_AUTH_VALUE}


def test_chunk_still_drops_unknown_native_values() -> None:
    assert _interrupt_to_standard_chunk("intr-x", {"type": "something_else"}) is None


def test_checkpoint_fallback_returns_the_pending_auth_interrupt() -> None:
    payloads = _interrupt_payloads_from_pending_writes(
        [("task-1", "__interrupt__", [Interrupt(value=dict(_AUTH_VALUE), id="intr-auth")])]
    )

    assert len(payloads) == 1
    assert payloads[0]["interrupt_id"] == "intr-auth"
    assert payloads[0]["payload"]["type"] == "auth_required"
    assert payloads[0]["payload"]["attempt"] == 2


class _StateBackedAgent:
    def __init__(self, state: SimpleNamespace) -> None:
        self._state = state

    async def aget_state(self, _config: dict[str, Any]) -> SimpleNamespace:
        return self._state


@pytest.mark.asyncio
async def test_reconnect_synthesizes_the_auth_interrupt() -> None:
    state = SimpleNamespace(
        interrupts=[],
        tasks=[
            SimpleNamespace(
                id="task-1",
                path=("__pregel_pull", "model"),
                interrupts=[Interrupt(value=dict(_AUTH_VALUE), id="intr-auth")],
            )
        ],
    )

    events = await pending_input_requested_events(
        _StateBackedAgent(state),
        {"configurable": {"thread_id": "thread-1"}},
        run_id="run-1",
        thread_id="thread-1",
        emitted=[],
    )

    assert len(events) == 1
    assert events[0]["data"]["interrupt_id"] == "intr-auth"
    assert events[0]["data"]["payload"]["type"] == "auth_required"
    assert events[0]["data"]["payload"]["attempt"] == 2


def test_rest_resume_check_now_sees_the_checkpointed_auth_interrupt() -> None:
    chunk = _interrupt_to_standard_chunk("intr-auth", dict(_AUTH_VALUE))

    stale = {"decisions": [{"type": "respond", "message": "retry", "attempt": 1}]}
    current = {"decisions": [{"type": "respond", "message": "retry", "attempt": 2}]}
    assert decision_problem(stale, chunk) is not None
    assert decision_problem({"decisions": [{"type": "approve"}]}, chunk) is not None
    assert decision_problem(current, chunk) is None


class _InterruptedAgent:
    def __init__(self, state: SimpleNamespace) -> None:
        self._state = state

    async def astream(self, *_args: Any, **_kwargs: Any) -> Any:
        from langgraph.errors import GraphInterrupt

        if False:
            yield  # pragma: no cover
        raise GraphInterrupt([])

    async def aget_state(self, _config: Any) -> SimpleNamespace:
        return self._state


@pytest.mark.asyncio
async def test_legacy_sse_uses_the_langgraph_interrupt_id() -> None:
    interrupt = Interrupt(value=dict(_AUTH_VALUE), id="intr-real")
    state = SimpleNamespace(tasks=[SimpleNamespace(interrupts=[interrupt])])

    events = [e async for e in stream_agent_response(_InterruptedAgent(state), [], {})]
    chunks = [
        json.loads(line[len("data: ") :])
        for raw in events
        if "event: interrupt\n" in raw
        for line in raw.split("\n")
        if line.startswith("data: ")
    ]

    assert [chunk["interrupt_id"] for chunk in chunks] == ["intr-real"]
    assert chunks[0]["type"] == "auth_required"


def test_oauth_callback_never_posts_to_any_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cors_allowed_origins", "https://app.example,https://b.example")
    credential_id = uuid.uuid4()

    known = bytes(_oauth_callback_html(credential_id, "https://app.example").body).decode()
    unknown = bytes(_oauth_callback_html(credential_id, None).body).decode()

    assert '["https://app.example"]' in known
    assert '["https://app.example", "https://b.example"]' in unknown
    assert '"*"' not in known and '"*"' not in unknown
    assert str(credential_id) in unknown


def test_oauth_callback_cannot_close_its_script() -> None:
    html = bytes(
        _oauth_callback_html(uuid.uuid4(), "https://x.example</script><script>").body
    ).decode()

    assert "</script><script>" not in html
