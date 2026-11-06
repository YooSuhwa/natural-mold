"""GuardrailMiddleware through Moldy's real agent build (guardrail-minimum-plan.md §3).

A fake chat model records every request it receives, so each test can check what would have
left for the provider, and the checkpoint shows what Moldy stored. All values are synthetic.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver

from app.agent_runtime.runtime_agent_builder import build_agent
from app.config import settings
from app.guardrails import actions, run
from app.guardrails.detectors import detect
from app.guardrails.middleware import Guard, GuardrailMiddleware, hash_key
from app.guardrails.rules import BUILTIN_TEMPLATES, Rule, RuleSet

RRN = "900101-1234567"
SECRET = "sk-abcdefghijklmnopqrstuvwx"
KR_DEFAULT = BUILTIN_TEMPLATES["kr-default"]


class RecordingModel(FakeMessagesListChatModel):
    """Answers from ``responses`` and keeps every message list it was sent."""

    seen: list[list[BaseMessage]] = []

    def bind_tools(self, tools: Any, **kwargs: Any) -> RecordingModel:
        return self

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> Any:
        self.seen.append(list(messages))
        return super()._generate(messages, *args, **kwargs)


def _model(*responses: AIMessage) -> RecordingModel:
    return RecordingModel(responses=list(responses), seen=[])


def _text(messages: list[BaseMessage]) -> str:
    return "\n".join(str(m.content) for m in messages) + "".join(
        str(getattr(m, "tool_calls", "")) for m in messages
    )


async def _run(
    model: RecordingModel,
    prompt: str,
    *,
    rules: RuleSet = KR_DEFAULT,
    tools: list[Any] | None = None,
    subagents: list[dict[str, Any]] | None = None,
) -> tuple[Any, RunnableConfig, Guard]:
    guard = Guard(rules, uuid.uuid4())
    agent = build_agent(
        model,
        tools or [],
        "You are a test agent.",
        checkpointer=MemorySaver(),
        subagents=subagents,
        guard=guard,
    )
    config: RunnableConfig = {"configurable": {"thread_id": str(uuid.uuid4())}}
    await agent.ainvoke({"messages": [{"type": "human", "content": prompt}]}, config)
    return agent, config, guard


async def _stored(agent: Any, config: RunnableConfig) -> list[BaseMessage]:
    return (await agent.aget_state(config)).values["messages"]


# --- ② what goes to the model ---------------------------------------------------------


async def test_identifiers_are_replaced_only_in_the_request() -> None:
    model = _model(AIMessage(content="확인했습니다."))

    agent, config, guard = await _run(model, f"주민번호 {RRN}로 조회해 주세요")

    sent = model.seen[0]
    assert RRN not in _text(sent)
    assert "[주민등록번호]" in _text(sent)
    stored = await _stored(agent, config)
    assert stored[0].content == f"주민번호 {RRN}로 조회해 주세요"
    assert ("kr-identifiers", "RRN", "redact") in {
        (d.rule, d.entity, d.action) for d in guard.decisions
    }


async def test_contacts_keep_their_last_digits() -> None:
    model = _model(AIMessage(content="네."))

    await _run(model, "연락처는 010-1234-5678입니다")

    assert "***-****-5678" in _text(model.seen[0])


async def test_a_secret_blocks_the_call() -> None:
    model = _model(AIMessage(content="보내면 안 되는 답"))

    agent, config, _ = await _run(model, f"이 키로 호출해: {SECRET}")

    assert model.seen == []
    reply = (await _stored(agent, config))[-1]
    assert isinstance(reply, AIMessage)
    assert "가드레일 정책(secrets)" in str(reply.content)
    assert SECRET not in str(reply.content)


async def test_observe_mode_changes_nothing_and_records() -> None:
    model = _model(AIMessage(content="네."))

    _, _, guard = await _run(model, f"주민번호 {RRN}", rules=BUILTIN_TEMPLATES["kr-observe"])

    assert RRN in _text(model.seen[0])
    assert {(d.entity, d.action) for d in guard.decisions} == {("RRN", "warn")}


# --- ③④ tools -------------------------------------------------------------------------


@tool
def lookup(query: str) -> str:
    """Look a customer up."""
    return f"고객 김민수, 주민번호 {RRN}, 조회어 {query}"


async def test_tool_args_with_a_secret_are_refused_before_the_tool_runs() -> None:
    called: list[str] = []

    @tool
    def send(body: str) -> str:
        """Send a message."""
        called.append(body)
        return "sent"

    call = {"name": "send", "args": {"body": f"키는 {SECRET}"}, "id": "c1", "type": "tool_call"}
    model = _model(AIMessage(content="", tool_calls=[call]), AIMessage(content="못 보냈어요"))

    agent, config, _ = await _run(model, "메시지 보내줘", tools=[send])

    assert called == []
    result = next(m for m in await _stored(agent, config) if isinstance(m, ToolMessage))
    assert result.status == "error"
    assert "다시 시도하지 마세요" in str(result.content)


async def test_tool_results_are_redacted_before_the_model_and_the_history() -> None:
    call = {"name": "lookup", "args": {"query": "김민수"}, "id": "c1", "type": "tool_call"}
    model = _model(AIMessage(content="", tool_calls=[call]), AIMessage(content="찾았습니다"))

    agent, config, _ = await _run(model, "김민수 고객 찾아줘", tools=[lookup])

    result = next(m for m in await _stored(agent, config) if isinstance(m, ToolMessage))
    assert RRN not in str(result.content) and "[주민등록번호]" in str(result.content)
    assert RRN not in _text(model.seen[1])


# --- paths the middleware does not cover by registration alone (plan §3.4.1) -------------


async def test_a_declarative_subagent_gets_its_own_guard() -> None:
    # ② only, so the root's ③ check cannot hide a missing child guard.
    send_only = RuleSet("send-only", 1, (Rule("ids", ("kr",), frozenset({"llm_send"}), "redact"),))
    child = _model(AIMessage(content="하위 작업 완료"))
    call = {
        "name": "task",
        "args": {"description": f"주민번호 {RRN} 확인", "subagent_type": "helper"},
        "id": "t1",
        "type": "tool_call",
    }
    root = _model(AIMessage(content="", tool_calls=[call]), AIMessage(content="끝"))
    helper = {"name": "helper", "description": "helps", "system_prompt": "help", "model": child}

    await _run(root, "도와줘", rules=send_only, subagents=[helper])

    assert child.seen, "the subagent model was not called"
    assert RRN not in _text(child.seen[0])
    assert "[주민등록번호]" in _text(child.seen[0])


async def test_the_summary_call_is_guarded() -> None:
    # A tiny context window makes Deep Agents summarize the earlier turns with the model.
    model = _model(
        AIMessage(content="요약: 고객 정보를 확인함"),
        AIMessage(content="답변"),
        AIMessage(content="답변"),
    )
    model.profile = {"max_input_tokens": 6000}
    long_turn = f"상담 기록 주민번호 {RRN} " + "내용 " * 600
    guard = Guard(KR_DEFAULT, uuid.uuid4())
    agent = build_agent(model, [], "You are a test agent.", checkpointer=MemorySaver(), guard=guard)
    config: RunnableConfig = {"configurable": {"thread_id": str(uuid.uuid4())}}
    history = [
        {"type": "human", "content": long_turn},
        {"type": "ai", "content": "확인했습니다 " + "응답 " * 600},
        {"type": "human", "content": long_turn},
        {"type": "ai", "content": "확인했습니다 " + "응답 " * 600},
        {"type": "human", "content": "마지막 질문"},
    ]

    await agent.ainvoke({"messages": history}, config)

    assert len(model.seen) >= 2, "the summarizer did not run"
    for request in model.seen:
        assert RRN not in _text(request)


# --- set-up ----------------------------------------------------------------------------


def test_guardrails_are_off_by_default_and_only_for_standard_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert run.rules_for("standard") is None
    monkeypatch.setattr(settings, "guardrail_default_template", "kr-default")
    assert run.rules_for("standard") is KR_DEFAULT
    assert run.rules_for("builder") is None


def test_an_unknown_template_fails_instead_of_running_unchecked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "guardrail_default_template", "kr-defualt")
    with pytest.raises(run.UnknownGuardrailTemplateError):
        run.rules_for("standard")


def test_guardrail_policy_is_a_lease_fallback() -> None:
    from app.agent_runtime.prepared_run_input_policy import is_legacy_fallback

    assert is_legacy_fallback("guardrail_policy")


def test_the_sync_agent_api_is_refused() -> None:
    middleware = GuardrailMiddleware(Guard(KR_DEFAULT, None))
    with pytest.raises(RuntimeError, match="async"):
        middleware.wrap_model_call(object(), lambda request: request)


# --- actions ---------------------------------------------------------------------------


def test_overlapping_findings_take_the_strongest_action() -> None:
    text = f"번호 {RRN}"
    findings = detect(text, ["kr"])
    rules = RuleSet(
        "t",
        1,
        (
            Rule("warn-all", ("kr",), frozenset({"llm_send"}), "warn"),
            Rule("redact-rrn", ("kr",), frozenset({"llm_send"}), "redact", frozenset({"RRN"})),
        ),
    )

    applied = actions.apply(text, findings, rules, "llm_send", b"k" * 32)

    assert applied.text == "번호 [주민등록번호]"
    assert [d.rule for d in applied.decisions] == ["redact-rrn"]


def test_hashes_are_stable_within_a_company_and_differ_across_companies() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    rules = RuleSet("t", 1, (Rule("h", ("kr",), frozenset({"llm_send"}), "hash"),))
    text = f"번호 {RRN}"
    findings = detect(text, ["kr"])

    first = actions.apply(text, findings, rules, "llm_send", hash_key(a)).text
    again = actions.apply(text, findings, rules, "llm_send", hash_key(a)).text
    other = actions.apply(text, findings, rules, "llm_send", hash_key(b)).text

    assert first == again != other
    assert first.startswith("번호 [주민등록번호:") and RRN not in first


# --- model calls outside the agent (plan §3.4.1) ------------------------------------------


class _FakeDb:
    def __init__(self, profile: str) -> None:
        self.profile = profile

    async def get(self, model: Any, key: Any) -> Any:
        from types import SimpleNamespace

        from app.models.guardrail_template import AgentGuardrailBinding

        if model is AgentGuardrailBinding:
            return None  # no agent choice: the installation default applies
        return SimpleNamespace(id=key, runtime_profile=self.profile, settings={})


@pytest.mark.parametrize(
    ("template", "profile", "expected"),
    [
        ("", "standard", f"사용자: 주민번호 {RRN}"),
        ("kr-default", "standard", "사용자: 주민번호 [주민등록번호]"),
        ("kr-default", "builder", f"사용자: 주민번호 {RRN}"),
    ],
)
async def test_the_followup_suggestion_transcript_is_guarded(
    monkeypatch: pytest.MonkeyPatch, template: str, profile: str, expected: str
) -> None:
    from types import SimpleNamespace

    from app.services import followup_service

    monkeypatch.setattr(settings, "guardrail_default_template", template)
    conversation = SimpleNamespace(agent_id=uuid.uuid4(), tenant_id=uuid.uuid4())

    guarded = await followup_service._guarded_transcript(
        _FakeDb(profile),  # type: ignore[arg-type]
        conversation,  # type: ignore[arg-type]
        f"사용자: 주민번호 {RRN}",
    )

    assert guarded == expected


async def test_a_blocked_followup_transcript_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from app.services import followup_service

    monkeypatch.setattr(settings, "guardrail_default_template", "kr-default")
    conversation = SimpleNamespace(agent_id=uuid.uuid4(), tenant_id=uuid.uuid4())

    guarded = await followup_service._guarded_transcript(
        _FakeDb("standard"),  # type: ignore[arg-type]
        conversation,  # type: ignore[arg-type]
        f"사용자: 키 {SECRET}",
    )

    assert guarded is None
