"""Guardrail records (plan §3.5): detection rows, block audits and Langfuse spans.

Runs Moldy's real agent build with a recording fake model, as in test_guardrail_middleware.
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_agent_builder import build_agent
from app.guardrails.events import RunScope, value_digest
from app.guardrails.middleware import Guard
from app.guardrails.rules import BUILTIN_TEMPLATES
from app.models.audit_event import AuditEvent
from app.models.conversation import Conversation
from app.models.guardrail_event import GuardrailEvent
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, TestSession, seed_agent
from tests.test_guardrail_middleware import RecordingModel

RRN = "900101-1234567"
SECRET = "sk-abcdefghijklmnopqrstuvwx"
KR_DEFAULT = BUILTIN_TEMPLATES["kr-default"]


async def _conversation(db: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    _, _, agent = await seed_agent(db)
    conversation = Conversation(
        agent_id=agent.id,
        title="guardrail",
        org_id=agent.org_id,
        tenant_id=agent.tenant_id,
        user_id=agent.user_id,
        agent_name_snapshot=agent.name,
        last_activity_at=datetime.now(UTC),
    )
    db.add(conversation)
    await db.commit()
    return conversation.id, agent.id


async def _run(prompt: str, scope: RunScope) -> Guard:
    guard = Guard(KR_DEFAULT, scope.org_context.tenant_id if scope.org_context else None, scope)
    model = RecordingModel(responses=[AIMessage(content="확인했습니다")], seen=[])
    agent = build_agent(model, [], "test", checkpointer=MemorySaver(), guard=guard)
    config: RunnableConfig = {"configurable": {"thread_id": str(uuid.uuid4())}}
    await agent.ainvoke({"messages": [{"type": "human", "content": prompt}]}, config)
    return guard


async def _rows() -> list[GuardrailEvent]:
    async with TestSession() as db:
        return list(await db.scalars(select(GuardrailEvent)))


async def test_a_detection_is_recorded_without_the_value(db: AsyncSession) -> None:
    conversation_id, agent_id = await _conversation(db)
    scope = RunScope(unit_context(TEST_USER_ID), conversation_id, agent_id, "run-1")

    guard = await _run(f"주민번호 {RRN}로 조회", scope)

    rows = [r for r in await _rows() if r.entity == "RRN"]
    assert [(r.stage, r.rule, r.action, r.count) for r in rows] == [
        ("llm_send", "kr-identifiers", "redact", 1)
    ]
    row = rows[0]
    assert (row.conversation_id, row.agent_id, row.run_id) == (conversation_id, agent_id, "run-1")
    assert (row.template, row.template_version) == ("kr-default", KR_DEFAULT.version)
    assert row.value_digests == [value_digest(guard.key, "RRN", RRN)]
    assert RRN not in json.dumps([r.__dict__ for r in rows], default=str)


async def test_a_block_is_audited(db: AsyncSession) -> None:
    conversation_id, agent_id = await _conversation(db)
    scope = RunScope(unit_context(TEST_USER_ID), conversation_id, agent_id, "run-2")

    await _run(f"이 키로 호출해 {SECRET}", scope)

    [blocked] = [r for r in await _rows() if r.action == "block"]
    assert (blocked.stage, blocked.rule, blocked.entity) == ("llm_send", "secrets", "SECRET")
    async with TestSession() as check:
        [event] = list(
            await check.scalars(select(AuditEvent).where(AuditEvent.action == "guardrail.block"))
        )
    assert (event.outcome, event.reason_code, event.target_id) == (
        "denied",
        "secrets",
        str(conversation_id),
    )
    assert event.event_metadata == {
        "template": "kr-default",
        "template_version": KR_DEFAULT.version,
        "stage": "llm_send",
        "entity": "SECRET",
    }
    assert SECRET not in json.dumps(event.event_metadata)


async def test_a_thread_that_is_not_a_conversation_still_records(db: AsyncSession) -> None:
    await seed_agent(db)
    scope = RunScope(unit_context(TEST_USER_ID), uuid.uuid4(), None, "run-3")

    await _run(f"주민번호 {RRN}", scope)

    rows = [r for r in await _rows() if r.entity == "RRN"]
    assert rows and all(r.conversation_id is None for r in rows)


async def test_no_scope_writes_nothing() -> None:
    guard = Guard(KR_DEFAULT, None)
    await guard.check([f"주민번호 {RRN}"], "llm_send")
    await guard.flush()

    assert await _rows() == []


async def test_each_check_is_a_guardrail_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.observability import langfuse

    spans: list[dict[str, Any]] = []

    class Span:
        def update(self, **kwargs: Any) -> None:
            spans[-1].update(kwargs)

    class Client:
        @contextmanager
        def start_as_current_observation(self, **kwargs: Any) -> Any:
            spans.append(dict(kwargs))
            yield Span()

    monkeypatch.setattr(langfuse, "is_langfuse_enabled", lambda: True)
    monkeypatch.setattr(langfuse, "_get_langfuse_client", lambda: Client())
    monkeypatch.setattr(langfuse, "_trace_id_for_run", lambda _client, run_id: f"trace-{run_id}")
    guard = Guard(KR_DEFAULT, None, RunScope(None, run_id="run-4"))

    await guard.check([f"주민번호 {RRN}"], "llm_send")
    await guard.check(["답변"], "output", record=False)  # display copies are not traced

    [span] = spans
    assert (span["as_type"], span["name"]) == ("guardrail", "guardrail.llm_send")
    assert span["trace_context"] == {"trace_id": "trace-run-4"}
    assert span["metadata"]["decisions"] >= 1 and span["metadata"]["blocked"] is False
    assert "duration_ms" in span["metadata"]
    assert RRN not in json.dumps(span, default=str)
