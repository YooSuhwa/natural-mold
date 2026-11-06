"""Natural pseudonyms for people (guardrail-minimum-plan.md §3.3.1, step 3-4).

A made-up Korean name stands in for a real one: same length, same final consonant on the
last syllable, so particles survive the round trip. All names are synthetic.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.guardrails.events import RunScope
from app.guardrails.middleware import Guard
from app.guardrails.pseudonyms import Pseudonyms, has_batchim, natural_candidates
from app.guardrails.rules import BUILTIN_TEMPLATES
from app.models.conversation import Conversation
from app.models.guardrail_pseudonym import GuardrailPseudonym
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, TestSession, seed_agent
from tests.test_guardrail_middleware import RecordingModel, _text
from tests.test_guardrail_pseudonyms import _invoke

KR_NATURAL = BUILTIN_TEMPLATES["kr-natural"]


def _map() -> Pseudonyms:
    return Pseudonyms(lambda: b"k" * 32)


@pytest.mark.parametrize("name", ["김민수", "박지훈", "이수", "최윤", "정하은", "강도윤"])
def test_the_pseudonym_keeps_length_and_final_consonant(name: str) -> None:
    alias = _map().token_for("PERSON", name, style="natural", context=name)

    assert len(alias) == len(name) and alias != name
    assert has_batchim(alias[-1]) == has_batchim(name[-1])


def test_a_pseudonym_never_reuses_a_name_or_given_name_already_in_play() -> None:
    names = _map()
    context = "김준호 팀장과 서아 님, 김민수 과장"

    alias = names.token_for("PERSON", "김민수", style="natural", context=context)

    assert alias not in context and alias[1:] not in context
    second = names.token_for("PERSON", "이민수", style="natural", context="이민수")
    assert second[1:] != alias[1:], "two people must not share a given name"


@pytest.mark.parametrize(
    ("form", "restored"),
    [
        ("{a}는 출장 중입니다", "김민수는 출장 중입니다"),
        ("{a}가 보냈어요", "김민수가 보냈어요"),
        ("{a}에게 전달", "김민수에게 전달"),
        ("{a}의 메일", "김민수의 메일"),
        ("{a} 과장님", "김민수 과장님"),
        ("{g} 씨에게", "민수 씨에게"),
        ("{g}씨, {g}님", "민수씨, 민수님"),
    ],
)
def test_full_and_given_name_forms_are_restored(form: str, restored: str) -> None:
    names = _map()
    alias = names.token_for("PERSON", "김민수", style="natural", context="김민수")

    assert names.restore(form.format(a=alias, g=alias[1:])) == restored


def test_a_surname_alone_is_not_restored() -> None:
    # "김 과장" could be anyone; guessing would put a wrong name in the answer.
    names = _map()
    alias = names.token_for("PERSON", "박민수", style="natural", context="박민수")

    assert names.restore(f"{alias[0]} 과장") == f"{alias[0]} 과장"


def test_a_given_name_inside_another_word_is_left() -> None:
    names = _map()
    alias = names.token_for("PERSON", "김민수", style="natural", context="김민수")

    assert names.restore(f"새{alias[1:]}가") == f"새{alias[1:]}가"


@pytest.mark.parametrize("name", ["남궁민수진", "John", "김"])
def test_names_it_cannot_shape_fall_back_to_tags(name: str) -> None:
    assert _map().token_for("PERSON", name, style="natural").startswith("<<PERSON_")
    assert natural_candidates(name) == [] or len(name) in (2, 3)


async def test_the_model_sees_a_made_up_name_and_the_answer_gets_the_real_one() -> None:
    model = RecordingModel(responses=[AIMessage(content="김준호 과장에게 전달했습니다.")], seen=[])

    agent, config = await _invoke(model, Guard(KR_NATURAL, None))

    sent = _text(model.seen[0])
    assert "김민수" not in sent and "김준호" in sent
    stored = (await agent.aget_state(config)).values["messages"]
    assert stored[-1].content == "김민수 과장에게 전달했습니다."


async def test_a_natural_pseudonym_is_kept_across_runs(db: AsyncSession) -> None:
    _, _, agent = await seed_agent(db)
    conversation = Conversation(
        agent_id=agent.id,
        title="natural",
        org_id=agent.org_id,
        tenant_id=agent.tenant_id,
        user_id=agent.user_id,
        agent_name_snapshot=agent.name,
        last_activity_at=datetime.now(UTC),
    )
    db.add(conversation)
    await db.commit()
    scope = RunScope(unit_context(TEST_USER_ID), conversation.id, None, str(uuid.uuid4()))

    first = RecordingModel(responses=[AIMessage(content="네")], seen=[])
    await _invoke(first, Guard(KR_NATURAL, None, scope))
    second = RecordingModel(responses=[AIMessage(content="네")], seen=[])
    await _invoke(second, Guard(KR_NATURAL, None, scope))

    assert "김준호" in _text(second.seen[0])
    async with TestSession() as check:
        [row] = list(await check.scalars(select(GuardrailPseudonym)))
    assert (row.token, row.entity) == ("김준호", "PERSON")
    assert "김민수" not in row.value_encrypted


async def test_a_second_pass_does_not_pseudonymize_the_pseudonym() -> None:
    # The summarizer wrapper and the main guard both check the same request.
    guard = Guard(KR_NATURAL, None)
    [first] = await guard.check(["담당자 김민수 과장에게 전달해 주세요"], "llm_send")
    [second] = await guard.check([first.text], "llm_send")

    assert second.text == first.text
    assert guard.pseudonyms.natural == {"김준호": "김민수"}


async def test_a_streamed_natural_pseudonym_is_restored() -> None:
    from langchain_core.runnables import RunnableConfig
    from langgraph.checkpoint.memory import MemorySaver

    from app.agent_runtime.runtime_agent_builder import build_agent
    from tests.test_guardrail_output_stream import CharStreamModel, _streamed_text
    from tests.test_guardrail_pseudonyms import PROMPT

    model = CharStreamModel(answers=[AIMessage(content="김준호 과장에게 전달했습니다.")])
    agent = build_agent(
        model, [], "test", checkpointer=MemorySaver(), guard=Guard(KR_NATURAL, None)
    )
    config: RunnableConfig = {"configurable": {"thread_id": str(uuid.uuid4())}}
    stream = await agent.astream_events(
        {"messages": [{"type": "human", "content": PROMPT}]}, config=config, version="v3"
    )
    events = [event async for event in stream]

    deltas, finished = _streamed_text(events)
    assert finished == "김민수 과장에게 전달했습니다."
    assert "김준호" not in deltas
