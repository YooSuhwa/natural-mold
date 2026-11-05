"""⑤ output through the v3 stream Moldy's chat uses (guardrail-minimum-plan.md §3.1, §5 1-2).

The model streams its answer three characters at a time, so every identifier is split across
deltas. Nothing raw may appear in any protocol event (what SSE, stored message events and
``values`` snapshots are built from), and the checkpoint stores the checked answer.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver

from app.agent_runtime.runtime_agent_builder import build_agent
from app.guardrails.middleware import Guard
from app.guardrails.rules import BUILTIN_TEMPLATES, Rule, RuleSet

RRN = "900101-1234567"
KR_DEFAULT = BUILTIN_TEMPLATES["kr-default"]


class CharStreamModel(BaseChatModel):
    """Streams each scripted answer in three-character chunks."""

    answers: list[AIMessage]
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "char-stream"

    def bind_tools(self, tools: Any, **kwargs: Any) -> CharStreamModel:
        return self

    def _next(self) -> AIMessage:
        answer = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        return answer

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next())])

    def _stream(self, *args: Any, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        raise NotImplementedError

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        answer = self._next()
        text = str(answer.content)
        for i in range(0, len(text), 3):
            yield ChatGenerationChunk(message=AIMessageChunk(content=text[i : i + 3]))
        if answer.tool_calls:
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": c["name"],
                            "args": json.dumps(c["args"]),
                            "id": c["id"],
                            "index": 0,
                        }
                        for c in answer.tool_calls
                    ],
                )
            )


async def _stream(
    answers: list[AIMessage], *, rules: RuleSet = KR_DEFAULT, tools: list[Any] | None = None
) -> tuple[list[Any], Any, RunnableConfig]:
    model = CharStreamModel(answers=answers)
    agent = build_agent(
        model, tools or [], "test", checkpointer=MemorySaver(), guard=Guard(rules, uuid.uuid4())
    )
    config: RunnableConfig = {"configurable": {"thread_id": str(uuid.uuid4())}}
    events: list[Any] = []
    stream = await agent.astream_events(
        {"messages": [{"type": "human", "content": "고객 정보 알려줘"}]},
        config=config,
        version="v3",
    )
    async for event in stream:
        events.append(event)
    return events, agent, config


def _wire(events: list[Any]) -> str:
    return json.dumps(events, ensure_ascii=False, default=str)


def _streamed_text(events: list[Any]) -> tuple[str, str]:
    """What a client shows: the concatenated deltas, and the finished text block."""
    deltas, finished = [], []
    for event in events:
        if event.get("method") != "messages":
            continue
        payload = event["params"]["data"][0]
        if not isinstance(payload, dict):
            continue
        if payload.get("event") == "content-block-delta":
            delta = payload.get("delta") or {}
            if delta.get("type") == "text-delta":
                deltas.append(delta.get("text", ""))
        if payload.get("event") == "content-block-finish":
            content = payload.get("content") or {}
            if content.get("type") == "text":
                finished.append(content.get("text", ""))
    return "".join(deltas), "".join(finished)


async def _final_answer(agent: Any, config: RunnableConfig) -> str:
    messages = (await agent.aget_state(config)).values["messages"]
    content = [m for m in messages if isinstance(m, AIMessage)][-1].content
    if isinstance(content, str):
        return content
    return "".join(b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text")


async def test_a_short_answer_is_checked_before_any_text_leaves() -> None:
    events, agent, config = await _stream([AIMessage(content=f"주민번호는 {RRN}입니다.")])

    assert RRN not in _wire(events)
    deltas, finished = _streamed_text(events)
    assert finished == "주민번호는 [주민등록번호]입니다."
    assert RRN not in deltas
    assert await _final_answer(agent, config) == "주민번호는 [주민등록번호]입니다."


async def test_a_long_answer_streams_in_checked_bursts() -> None:
    filler = "안내 문장입니다. " * 30
    answer = f"{filler}고객 주민번호는 {RRN}이고 {filler}끝."

    events, agent, config = await _stream([AIMessage(content=answer)])

    assert RRN not in _wire(events)
    deltas, finished = _streamed_text(events)
    assert len(deltas) > 128, "the start of a long answer should stream before the end"
    assert RRN not in deltas
    assert finished == answer.replace(RRN, "[주민등록번호]")
    assert finished.startswith(deltas)
    assert RRN not in await _final_answer(agent, config)


async def test_a_blocking_output_rule_replaces_the_answer() -> None:
    rules = RuleSet(
        "block-out",
        1,
        (Rule("no-rrn", ("kr",), frozenset({"output"}), "block", frozenset({"RRN"})),),
    )

    events, agent, config = await _stream([AIMessage(content=f"번호 {RRN}")], rules=rules)

    assert RRN not in _wire(events)
    assert "가드레일 정책(no-rrn)" in await _final_answer(agent, config)


@tool
def lookup(name: str) -> str:
    """Look a customer up."""
    return f"{name} 고객, 주민번호 {RRN}"


async def test_tool_output_on_the_tools_channel_is_checked() -> None:
    call = {"name": "lookup", "args": {"name": "김민수"}, "id": "c1", "type": "tool_call"}
    events, _, _ = await _stream(
        [AIMessage(content="", tool_calls=[call]), AIMessage(content="찾았습니다")], tools=[lookup]
    )

    assert any(e.get("method") == "tools" for e in events), "no tools events to check"
    assert RRN not in _wire(events)


async def test_without_output_rules_no_transformer_is_installed() -> None:
    from app.guardrails.middleware import GuardrailMiddleware

    send_only = RuleSet("send", 1, (Rule("ids", ("kr",), frozenset({"llm_send"}), "redact"),))

    assert GuardrailMiddleware(Guard(send_only, None)).transformers == ()
    assert len(GuardrailMiddleware(Guard(KR_DEFAULT, None)).transformers) == 1


async def test_a_value_split_at_the_release_point_is_held_until_it_is_whole() -> None:
    from app.guardrails.middleware import _StreamGuard
    from app.guardrails.stream import BURST, LOOKBACK, StreamRedactor

    redactor = StreamRedactor(_StreamGuard(Guard(KR_DEFAULT, None)))
    head = "가" * (LOOKBACK + BURST - 4)
    released = [
        await redactor.feed(("r", 0, "text"), piece, "output")
        for piece in (head, "번호 9001", "01-1234567 확인", "다" * (LOOKBACK + BURST))
    ]

    assert RRN not in "".join(released)
    assert "[주민등록번호]" in "".join(released)


async def test_moldys_protocol_ingestion_carries_only_checked_text() -> None:
    from app.agent_runtime.langgraph_stream_ingestion import iter_ingested_protocol_events

    model = CharStreamModel(answers=[AIMessage(content=f"주민번호는 {RRN}입니다.")])
    agent = build_agent(
        model, [], "test", checkpointer=MemorySaver(), guard=Guard(KR_DEFAULT, uuid.uuid4())
    )
    thread = str(uuid.uuid4())
    stored = [
        ingested.event
        async for ingested in iter_ingested_protocol_events(
            agent,
            {"messages": [{"type": "human", "content": "알려줘"}]},
            {"configurable": {"thread_id": thread}},
            run_id="run-1",
            thread_id=thread,
        )
    ]

    assert stored, "no protocol events"
    assert RRN not in _wire(stored)
    assert "[주민등록번호]" in _wire(stored)
