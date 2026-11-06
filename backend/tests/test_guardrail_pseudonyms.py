"""Pseudonymization and restore (guardrail-minimum-plan.md §3.3.1, §5 9).

The model sees ``<<PERSON_1>>``; the user, the stored history and (where a rule allows) the
tools see the real name. Fake models answer with the token the guard issues, as a real
model would echo it.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_agent_builder import build_agent
from app.guardrails.actions import apply
from app.guardrails.detectors import detect
from app.guardrails.events import RunScope
from app.guardrails.middleware import Guard
from app.guardrails.pseudonyms import MAX_TOKEN_LENGTH
from app.guardrails.rules import BUILTIN_TEMPLATES, Rule, RuleSet
from app.guardrails.stream import LOOKBACK
from app.models.conversation import Conversation
from app.models.guardrail_pseudonym import GuardrailPseudonym
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, TestSession, seed_agent
from tests.test_guardrail_middleware import RecordingModel, _text
from tests.test_guardrail_output_stream import CharStreamModel, _streamed_text, _wire

NAME = "김민수"
KR_DEFAULT = BUILTIN_TEMPLATES["kr-default"]
PROMPT = f"담당자 {NAME} 과장에게 전달해 주세요"


def _people(restore_for_tools: bool) -> RuleSet:
    return RuleSet(
        "people",
        1,
        (
            Rule(
                "people",
                ("kr",),
                frozenset({"llm_send"}),
                "pseudonymize",
                frozenset({"PERSON"}),
                restore_for_tools=restore_for_tools,
            ),
        ),
    )


async def _conversation(db: AsyncSession) -> uuid.UUID:
    _, _, agent = await seed_agent(db)
    conversation = Conversation(
        agent_id=agent.id,
        title="pseudonyms",
        org_id=agent.org_id,
        tenant_id=agent.tenant_id,
        user_id=agent.user_id,
        agent_name_snapshot=agent.name,
        last_activity_at=datetime.now(UTC),
    )
    db.add(conversation)
    await db.commit()
    return conversation.id


async def _invoke(
    model: Any, guard: Guard, prompt: str = PROMPT, tools: list[Any] | None = None
) -> tuple[Any, RunnableConfig]:
    agent = build_agent(model, tools or [], "test", checkpointer=MemorySaver(), guard=guard)
    config: RunnableConfig = {"configurable": {"thread_id": str(uuid.uuid4())}}
    await agent.ainvoke({"messages": [{"type": "human", "content": prompt}]}, config)
    return agent, config


async def test_the_model_sees_a_pseudonym_and_the_history_keeps_the_name() -> None:
    model = RecordingModel(
        responses=[AIMessage(content="<<PERSON_1>> 과장에게 전달했습니다.")], seen=[]
    )

    agent, config = await _invoke(model, Guard(KR_DEFAULT, None))

    sent = _text(model.seen[0])
    assert NAME not in sent and "<<PERSON_1>>" in sent
    stored = (await agent.aget_state(config)).values["messages"]
    assert stored[0].content == PROMPT
    assert stored[-1].content == f"{NAME} 과장에게 전달했습니다."


async def test_the_same_person_keeps_one_pseudonym_and_others_get_their_own() -> None:
    guard = Guard(_people(False), None)
    text = f"{PROMPT}. {NAME} 과장과 박지훈 대리가 참석합니다"

    [applied] = await guard.check([text], "llm_send")

    assert applied.text.count("<<PERSON_1>>") == 2
    assert "<<PERSON_2>>" in applied.text and "박지훈" not in applied.text


def _mail_tool(received: list[str]) -> Any:
    @tool
    def mail(to: str) -> str:
        """Send a mail to a person."""
        received.append(to)
        return "sent"

    return mail


async def test_tools_get_the_name_back_only_when_the_rule_allows() -> None:
    for restore, expected in ((True, NAME), (False, "<<PERSON_1>>")):
        received: list[str] = []
        call = {"name": "mail", "args": {"to": "<<PERSON_1>>"}, "id": "c1", "type": "tool_call"}
        model = RecordingModel(
            responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="보냈어요")],
            seen=[],
        )

        await _invoke(model, Guard(_people(restore), None), tools=[_mail_tool(received)])

        assert received == [expected], restore


async def test_the_map_is_kept_per_conversation_and_encrypted(db: AsyncSession) -> None:
    conversation_id = await _conversation(db)
    scope = RunScope(unit_context(TEST_USER_ID), conversation_id, None, "run-1")
    first = RecordingModel(responses=[AIMessage(content="네")], seen=[])
    await _invoke(
        first,
        Guard(KR_DEFAULT, None, scope),
        prompt=f"담당자 박지훈 대리와 {NAME} 과장이 참석합니다",
    )

    second = RecordingModel(responses=[AIMessage(content="네")], seen=[])
    await _invoke(second, Guard(KR_DEFAULT, None, scope), prompt=f"{NAME} 과장 건")

    # A new run of the same conversation reuses the pseudonym the first run issued.
    assert "<<PERSON_2>>" in _text(second.seen[0])
    async with TestSession() as check:
        rows = list(await check.scalars(select(GuardrailPseudonym)))
    assert sorted(r.token for r in rows) == ["<<PERSON_1>>", "<<PERSON_2>>"]
    assert all(r.conversation_id == conversation_id for r in rows)
    assert NAME not in json.dumps([r.value_encrypted for r in rows])


async def test_an_invented_pseudonym_is_left_as_it_is() -> None:
    model = RecordingModel(responses=[AIMessage(content="<<PERSON_7>>에게 물어보세요")], seen=[])
    guard = Guard(KR_DEFAULT, None)

    agent, config = await _invoke(model, guard)

    assert (await agent.aget_state(config)).values["messages"][-1].content == (
        "<<PERSON_7>>에게 물어보세요"
    )
    assert guard.pseudonyms.invented == 1


async def test_a_streamed_pseudonym_split_across_deltas_is_restored() -> None:
    model = CharStreamModel(answers=[AIMessage(content="<<PERSON_1>> 과장에게 전달했습니다.")])
    agent = build_agent(
        model, [], "test", checkpointer=MemorySaver(), guard=Guard(KR_DEFAULT, None)
    )
    config: RunnableConfig = {"configurable": {"thread_id": str(uuid.uuid4())}}
    events = []
    stream = await agent.astream_events(
        {"messages": [{"type": "human", "content": PROMPT}]}, config=config, version="v3"
    )
    async for event in stream:
        events.append(event)

    _, finished = _streamed_text(events)
    assert finished == f"{NAME} 과장에게 전달했습니다."
    assert "<<PERSON_1>>" not in _wire([e for e in events if e.get("method") == "messages"])


def test_without_a_map_a_pseudonymize_rule_redacts() -> None:
    text = PROMPT
    applied = apply(text, detect(text, ["kr"]), _people(False), "llm_send", b"k" * 32)

    assert NAME not in applied.text and "[이름]" in applied.text


def test_the_stream_holds_back_at_least_one_whole_token() -> None:
    assert LOOKBACK >= MAX_TOKEN_LENGTH
