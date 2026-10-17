"""G-03: the ``auth_required`` interrupt and its resume, end to end (plan 11.5).

Done-when: ``retry``/``skip``/``cancel`` behave on resume even when the connection was
repaired between the ask and the answer (the latest recorded answer wins), the answer
is persisted on the resume run, a ``retry`` resume re-resolves the agent context (new
credentials), and a reload returns the pending interrupt with its current attempt.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from langchain_core.messages import AIMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime.connection_guard_policy import CredentialState
from tests.authz.f07_support import (
    ME,
    Ledger,
    calls,
    guard,
    guarded,
    interrupts,
    plain_tool,
    respond,
    run_graph,
    tool_messages,
)
from tests.authz.g03_support import (
    auth_payload,
    commands_url,
    respond_command,
    resume_runs,
    seed_conversation,
    seed_interrupted,
)

DONE = AIMessage(content="done")
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _run(ledger: Ledger) -> Any:
    ledger.states["c1"] = CredentialState("expired", ME, "mcp_oauth2")
    tools = [plain_tool(ledger, "a"), plain_tool(ledger, "free")]
    middleware = [guard(ledger, guarded={"a": guarded("a", "c1")})]
    return run_graph([calls("a", "free"), DONE, DONE], tools, middleware)


def _codes(result: dict[str, Any]) -> dict[str, str]:
    return {
        cid: m.artifact["connection_guard"]["code"]
        for cid, m in tool_messages(result).items()
        if isinstance(m.artifact, dict) and "connection_guard" in m.artifact
    }


async def test_cancel_is_honoured_when_the_connection_was_repaired_meanwhile() -> None:
    ledger = Ledger(flip_after=1)  # broken when asked, active by the time the user answers
    run = _run(ledger)
    await run.start()
    done = await run.resume(respond("cancel"))

    assert ledger.ran == []
    assert set(_codes(done).values()) == {"cancelled"}


async def test_skip_is_honoured_when_the_connection_was_repaired_meanwhile() -> None:
    ledger = Ledger(flip_after=1)
    run = _run(ledger)
    await run.start()
    done = await run.resume(respond("skip"))

    assert ledger.ran == ["free"]
    assert _codes(done) == {"call-a": "skipped"}


async def test_the_latest_answer_wins_after_a_still_broken_retry() -> None:
    ledger = Ledger(flip_after=3)  # start, re-run, retry read: broken; then repaired
    run = _run(ledger)
    await run.start()
    again = await run.resume(respond("retry"))
    assert interrupts(again)[0]["attempt"] == 2
    done = await run.resume(respond("cancel"))

    assert ledger.ran == []
    assert set(_codes(done).values()) == {"cancelled"}


async def test_retry_after_connecting_runs_the_call() -> None:
    ledger = Ledger(flip_after=1)
    run = _run(ledger)
    await run.start()
    await run.resume(respond("retry"))

    assert sorted(ledger.ran) == ["a", "free"]


@pytest.mark.asyncio
async def test_input_respond_retry_re_resolves_the_run_and_records_the_answer(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation = await seed_conversation(db)
    await seed_interrupted(db, conversation, auth_payload(1), auth_payload(2))
    started: dict[str, Any] = {}
    resolved: list[Any] = []

    async def fake_start(**kwargs: Any) -> None:
        started.update(kwargs)

    from app.routers import conversation_protocol_fork

    real_resolve = conversation_protocol_fork.resolve_agent_context

    async def spy_resolve(*args: Any, **kwargs: Any) -> Any:
        resolved.append(args[1])
        return await real_resolve(*args, **kwargs)

    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol.start_conversation_run", fake_start
    )
    monkeypatch.setattr(conversation_protocol_fork, "resolve_agent_context", spy_resolve)
    answer = {"decisions": [{"type": "respond", "message": "retry", "attempt": 2}]}

    result = await client.post(commands_url(conversation), json=respond_command(answer))

    assert result.status_code == 200, result.json()
    assert resolved == [conversation.id]  # the resumed agent (and its credentials) is rebuilt
    assert started["input_payload"] == answer and started["moldy_source"] == "resume"
    (run,) = await resume_runs(db, conversation)
    assert run.metadata_json is not None
    assert run.metadata_json["resume_decisions"] == [
        {"interrupt_id": "intr-1", "kind": "auth_required", "decision": "retry", "attempt": 2}
    ]


@pytest.mark.asyncio
async def test_reload_returns_the_pending_auth_interrupt_with_its_current_attempt(
    client: AsyncClient, db: AsyncSession
) -> None:
    conversation = await seed_conversation(db)
    await seed_interrupted(db, conversation, auth_payload(1), auth_payload(2))

    state = await client.get(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/state"
    )

    assert state.status_code == 200
    (task,) = state.json()["tasks"]
    (pending,) = task["interrupts"]
    assert pending["id"] == "intr-1"
    assert pending["value"]["type"] == "auth_required"
    assert pending["value"]["attempt"] == 2
    assert pending["value"]["allowed_decisions"] == ["respond"]
