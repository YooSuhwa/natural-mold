"""Workflow AI editing eval (workflow-plan W5): real tasks through the company LiteLLM gateway.

Run from ``backend/``: ``.venv/bin/python -m evals.workflow_edit.run``. Needs
``LITELLM_BASE_URL`` (ends in ``/v1``) and ``LITELLM_API_KEY``; the key is never printed or
cached. Replies are cached by request hash in ``cache/llm.json``, so a re-run costs nothing.

Each case starts from a draft (empty or the Q3 template), sends one request through the
same ``ai_edit.propose`` the API uses, and scores the result: the reply kind, whether the
proposed graph passes the publish checks, the node kinds it uses, the agents it picks.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, convert_to_openai_messages

from app.workflows.ai_edit import ModelTurn, OfferedAgent, propose, tool_spec
from app.workflows.checks import loop_shape
from app.workflows.definition import (
    RefSegment,
    WorkflowDefinition,
    agent_uses,
    empty_definition,
)
from evals.memory_recall.gateway import _hash, _post

HERE = Path(__file__).parent
CACHE = HERE / "cache" / "llm.json"
MODELS = ("gpt-5.4-mini", "claude-sonnet-5")

AGENTS = [
    OfferedAgent(
        "11111111-1111-4111-8111-111111111111",
        "문의 분류 에이전트",
        "고객 문의를 유형과 긴급도로 분류",
    ),
    OfferedAgent(
        "22222222-2222-4222-8222-222222222222", "답변 작성 에이전트", "고객에게 보낼 답변 초안 작성"
    ),
    OfferedAgent(
        "33333333-3333-4333-8333-333333333333",
        "주간 보고 에이전트",
        "팀 업무 메모로 주간 보고서 작성",
    ),
    OfferedAgent(
        "44444444-4444-4444-8444-444444444444",
        "웹 조사 에이전트",
        "주제에 대한 웹 자료 조사와 정리",
    ),
    OfferedAgent(
        "55555555-5555-4555-8555-555555555555",
        "품질 검사 에이전트",
        "글이 요구사항을 충족하는지 검사하고 고칠 점을 적음",
    ),
]
CLASSIFY, WRITE, WEEKLY, RESEARCH, REVIEW = (agent.id for agent in AGENTS)


def _q3() -> WorkflowDefinition:
    """The Q3 template (frontend ``inquiryTemplate``) with the eval's agent ids."""
    text = lambda t: {"text": t}  # noqa: E731
    ref = lambda n, p: {"ref": {"node": n, "path": p}}  # noqa: E731
    return WorkflowDefinition.model_validate(
        {
            "inputs": [
                {"name": "inquiry", "type": "string", "required": True, "label": "문의 내용"}
            ],
            "nodes": [
                {"id": "start", "type": "start"},
                {
                    "id": "classify",
                    "type": "agent",
                    "label": "문의 분류",
                    "agent_id": CLASSIFY,
                    "prompt": [text("분류: "), ref("input", "/inquiry")],
                    "output_schema": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string"},
                            "urgent": {"type": "boolean"},
                        },
                        "required": ["category", "urgent"],
                    },
                },
                {
                    "id": "is_urgent",
                    "type": "decision_bool",
                    "label": "긴급한가",
                    "condition": {
                        "all": [
                            {"source": "classify", "path": "/urgent", "op": "eq", "value": True}
                        ]
                    },
                },
                {
                    "id": "review",
                    "type": "approval",
                    "label": "담당자 승인",
                    "title": [text("긴급 문의")],
                    "timeout_minutes": 1440,
                },
                {
                    "id": "draft",
                    "type": "agent",
                    "label": "답변 초안",
                    "agent_id": WRITE,
                    "prompt": [ref("input", "/inquiry")],
                    "output_schema": {
                        "type": "object",
                        "properties": {"answer": {"type": "string"}},
                        "required": ["answer"],
                    },
                },
                {
                    "id": "done",
                    "type": "output",
                    "label": "출력",
                    "fields": {"answer": [ref("draft", "/answer")]},
                },
                {"id": "rejected", "type": "fail", "label": "거절 종료", "reason": "거절"},
            ],
            "edges": [
                {"from": "start", "to": "classify"},
                {"from": "classify", "to": "is_urgent"},
                {"from": "is_urgent", "to": "review", "when": "true"},
                {"from": "is_urgent", "to": "draft", "when": "false"},
                {"from": "review", "to": "draft", "when": "approved"},
                {"from": "review", "to": "rejected", "when": "rejected"},
                {"from": "draft", "to": "done"},
            ],
        }
    )


@dataclass(frozen=True)
class Case:
    name: str
    request: str
    start: str = "empty"  # or "q3"
    kind: str = "proposal"
    kinds: frozenset[str] = frozenset()
    agents: tuple[str, ...] = ()
    check: str | None = None  # an extra named check on the proposal


CASES = [
    Case(
        "q3_from_scratch",
        "고객 문의를 받아 문의 분류 에이전트로 유형과 긴급 여부를 나누고, "
        "긴급하면 내 승인을 받은 뒤 "
        "답변 작성 에이전트가 답변 초안을 쓰게 해 줘. "
        "승인을 거절하면 실패로 끝내고, 최종 결과로 답변을 내보내.",
        kinds=frozenset({"start", "agent", "decision_bool", "approval", "output", "fail"}),
        agents=(CLASSIFY, WRITE),
    ),
    Case(
        "weekly_report",
        "팀 업무 메모를 입력으로 받아 주간 보고 에이전트로 보고서를 쓰고, "
        "보고서에 위험 항목이 있다고 "
        "표시되면 내 승인을 받은 다음 내보내 줘. "
        "위험이 없으면 바로 내보내. 거절하면 실패로 끝내.",
        kinds=frozenset({"start", "agent", "decision_bool", "approval", "output", "fail"}),
        agents=(WEEKLY,),
    ),
    Case(
        "research_summary",
        "주제를 입력받아 웹 조사 에이전트로 자료를 모은 다음, "
        "답변 작성 에이전트가 그 자료로 요약문을 쓰게 하고, "
        "결과를 제목과 본문 두 필드로 나눠 출력해 줘.",
        kinds=frozenset({"start", "agent", "output"}),
        agents=(RESEARCH, WRITE),
        check="research_order",
    ),
    Case(
        "edit_timeout",
        "담당자 승인 기한을 2시간으로 바꿔 줘. 다른 건 그대로 둬.",
        start="q3",
        kinds=frozenset({"start", "agent", "decision_bool", "approval", "output", "fail"}),
        check="only_timeout",
    ),
    Case(
        "rewrite_loop",
        "요구사항을 입력받아 답변 작성 에이전트가 글을 쓰고 품질 검사 에이전트가 검사하게 해 줘. "
        "통과하지 못하면 검사 의견을 반영해서 다시 쓰게 하고, 최대 3번까지만 해. "
        "3번 해도 안 되면 내 승인을 받고, 승인하면 마지막 글을 내보내고 거절하면 실패로 끝내.",
        kinds=frozenset({"start", "agent", "decision_bool", "approval", "output", "fail"}),
        agents=(WRITE, REVIEW),
        check="loop",
    ),
    Case("ambiguous", "문서 처리하는 흐름 하나 만들어 줘.", kind="question"),
    # The remaining nodes (workflow-nodes-plan): for_each + llm, switch, ask + notify, llm decision.
    Case(
        "fanout_research",
        "주제를 입력받아 웹 조사 에이전트가 하위 주제 목록을 만들고, "
        "하위 주제마다 웹 조사 에이전트로 자세히 조사한 다음, "
        "조사 결과 전체를 에이전트 없이 모델로 한 번에 요약해서 출력해 줘.",
        kinds=frozenset({"start", "agent", "for_each", "llm", "output"}),
        agents=(RESEARCH,),
    ),
    Case(
        "switch_triage",
        "분류 결과의 category가 refund면 담당자 승인을 거쳐 답변을 쓰고, bug면 바로 답변 작성 "
        "에이전트가 답변을 쓰고, 그 밖의 값이면 실패로 끝내도록 여러 갈래 판단으로 바꿔 줘. "
        "긴급 여부 판단은 빼.",
        start="q3",
        kinds=frozenset({"start", "agent", "switch", "approval", "output", "fail"}),
        check="switch_cases",
    ),
    Case(
        "ask_then_notify",
        "주간 보고 에이전트가 보고서를 쓰기 전에 나에게 이번 주에 강조할 점을 물어보고 "
        "(하루 안에 답이 없으면 실패로 끝내), 그 답을 반영해 보고서를 쓴 뒤 "
        "나에게 앱 알림을 보내고 보고서를 내보내 줘.",
        kinds=frozenset({"start", "ask", "agent", "notify", "output", "fail"}),
        agents=(WEEKLY,),
    ),
    Case(
        "llm_judge",
        "고객 문의를 받아 답변 작성 에이전트가 답변을 쓰면, "
        "답변이 정중한지는 규칙으로 정할 수 없으니 모델이 판단하게 해서 "
        "정중하면 답변을 내보내고 아니면 실패로 끝내 줘.",
        kinds=frozenset({"start", "agent", "decision_bool", "output", "fail"}),
        agents=(WRITE,),
        check="llm_decision",
    ),
]


def _load_cache() -> dict[str, Any]:
    return json.loads(CACHE.read_text()) if CACHE.exists() else {}


USED: set[str] = set()


def gateway_model(name: str, cache: dict[str, Any], calls: list[int], used: set[str] = USED):
    """An ``EditModel`` over the gateway's chat completions, forcing the WorkflowEdit tool."""

    async def call(messages: list[Any]) -> ModelTurn:
        body = {
            "model": name,
            "messages": convert_to_openai_messages(messages),
            "tools": [tool_spec()],
            "tool_choice": {"type": "function", "function": {"name": "WorkflowEdit"}},
        }
        key = _hash(json.dumps(body, ensure_ascii=False, sort_keys=True))
        used.add(key)
        if key not in cache:
            calls[0] += 1
            reply = await asyncio.to_thread(_post, "/chat/completions", body)
            message = reply["choices"][0]["message"]
            usage = reply.get("usage") or {}
            cache[key] = {
                "tool_calls": [
                    {
                        "id": c["id"],
                        "name": c["function"]["name"],
                        "arguments": c["function"]["arguments"],
                    }
                    for c in message.get("tool_calls") or []
                ],
                "tokens": [usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)],
            }
        entry = cache[key]
        first = entry["tool_calls"][0] if entry["tool_calls"] else None
        try:
            args = json.loads(first["arguments"]) if first else None
        except json.JSONDecodeError:
            args = None
        raw = AIMessage(
            content="",
            tool_calls=[{"name": first["name"], "args": args or {}, "id": first["id"]}]
            if first
            else [],
        )
        return ModelTurn(
            args=args,
            raw=raw,
            tool_call_id=first["id"] if first else None,
            tokens_in=entry["tokens"][0],
            tokens_out=entry["tokens"][1],
        )

    return call


@dataclass
class Result:
    model: str
    case: str
    kind: str
    ok: bool
    notes: list[str] = field(default_factory=list)
    tokens: tuple[int, int] = (0, 0)
    attempts: int = 1
    message: str = ""
    plan: list[str] = field(default_factory=list)
    definition: dict[str, Any] | None = None


def _loop_notes(definition: WorkflowDefinition) -> list[str]:
    """One bounded loop (3 rounds) whose limit asks for approval, and the rewrite reads the
    previous round's review."""
    loops = loop_shape(definition).loops
    if len(loops) != 1:
        return [f"loops {len(loops)}"]
    (loop,) = loops
    nodes = {node.id: node for node in definition.nodes}
    notes: list[str] = []
    decision = nodes[loop.decision]
    rounds = (
        decision.repeat.max_rounds if decision.type == "decision_bool" and decision.repeat else None
    )
    if rounds != 3:
        notes.append(f"max_rounds {rounds}")
    limit = [e for e in definition.edges if e.from_ == loop.decision and e.when == "limit"]
    if not limit or nodes[limit[0].to].type != "approval":
        notes.append("limit exit does not ask for approval")
    reads_previous = any(
        node.type == "agent"
        and node.agent_id == WRITE
        and any(isinstance(seg, RefSegment) and seg.ref.previous for seg in node.prompt)
        for node in definition.nodes
        if node.id in loop.body
    )
    if not reads_previous:
        notes.append("the rewrite does not read the previous review")
    return notes


def score(case: Case, base: WorkflowDefinition, proposal: Any) -> tuple[bool, list[str]]:
    notes: list[str] = []
    if proposal.kind != case.kind:
        notes.append(f"kind {proposal.kind} (expected {case.kind})")
        if proposal.problems:
            notes.extend(proposal.problems[:3])
        return False, notes
    if case.kind == "question":
        return True, [proposal.message[:120]]
    definition = WorkflowDefinition.model_validate(proposal.definition)
    if proposal.issues:
        notes.extend(f"issue {issue.code} ({issue.node_id})" for issue in proposal.issues)
    kinds = {node.type for node in definition.nodes}
    if not case.kinds <= kinds:
        notes.append(f"missing kinds {sorted(case.kinds - kinds)}")
    used = [use.agent_id for use in agent_uses(list(definition.nodes))]
    if case.agents and sorted(set(used)) != sorted(set(case.agents)):
        notes.append(f"agents {used}")
    if case.check == "research_order":
        order = {node.agent_id: node.id for node in definition.nodes if node.type == "agent"}
        edges = {(edge.from_, edge.to) for edge in definition.edges}
        if (
            order.get(RESEARCH)
            and order.get(WRITE)
            and (order[RESEARCH], order[WRITE]) not in edges
        ):
            notes.append("research does not lead into the summary")
        outputs = [node for node in definition.nodes if node.type == "output"]
        if not outputs or len(outputs[0].fields) < 2:
            notes.append("output does not have two fields")
    if case.check == "loop":
        notes += _loop_notes(definition)
    if case.check == "switch_cases":
        switches = [node for node in definition.nodes if node.type == "switch"]
        values = {str(case.value) for node in switches for case in node.cases}
        if not switches or not {"refund", "bug"} <= values:
            notes.append(f"switch cases {sorted(values)}")
    llm_decisions = [
        node
        for node in definition.nodes
        if node.type == "decision_bool" and node.evaluator == "llm"
    ]
    if case.check == "llm_decision" and not llm_decisions:
        notes.append("no llm decision")
    if case.check == "only_timeout":
        before = {node.id: node.model_dump() for node in base.nodes}
        after = {node.id: node.model_dump() for node in definition.nodes}
        changed = sorted(n for n in before.keys() | after.keys() if before.get(n) != after.get(n))
        review = after.get("review", {})
        if changed != ["review"] or review.get("timeout_minutes") != 120:
            notes.append(f"changed {changed}, timeout {review.get('timeout_minutes')}")
        if base.edges != definition.edges:
            notes.append("edges changed")
    return not notes, notes


async def main() -> None:
    cache = _load_cache()
    calls = [0]
    results: list[Result] = []
    for model_name in MODELS:
        model = gateway_model(model_name, cache, calls)
        for case in CASES:
            base = (
                _q3()
                if case.start == "q3"
                else WorkflowDefinition.model_validate(empty_definition())
            )
            proposal = await propose(base, AGENTS, case.request, [], model=model)
            ok, notes = score(case, base, proposal)
            results.append(
                Result(
                    model_name,
                    case.name,
                    proposal.kind,
                    ok,
                    notes,
                    (proposal.tokens_in, proposal.tokens_out),
                    proposal.attempts,
                    proposal.message,
                    proposal.plan,
                    proposal.definition,
                )
            )
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            # Only the replies this run used: a changed prompt leaves no stale entries behind.
            kept = {key: cache[key] for key in sorted(USED) if key in cache}
            CACHE.write_text(json.dumps(kept, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    (HERE / "results.json").write_text(
        json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=1, default=list) + "\n"
    )
    for r in results:
        verdict = "PASS" if r.ok else "FAIL"
        print(f"{r.model:16} {r.case:18} {r.kind:9} {verdict} x{r.attempts} {r.tokens}", *r.notes)
    passed = sum(r.ok for r in results)
    print(f"{passed}/{len(results)} passed, {calls[0]} gateway calls")


if __name__ == "__main__":
    asyncio.run(main())
