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
from app.workflows.definition import WorkflowDefinition, empty_definition
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
]
CLASSIFY, WRITE, WEEKLY, RESEARCH = (agent.id for agent in AGENTS)


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
    Case("ambiguous", "문서 처리하는 흐름 하나 만들어 줘.", kind="question"),
]


def _load_cache() -> dict[str, Any]:
    return json.loads(CACHE.read_text()) if CACHE.exists() else {}


def gateway_model(name: str, cache: dict[str, Any], calls: list[int]):
    """An ``EditModel`` over the gateway's chat completions, forcing the WorkflowEdit tool."""

    async def call(messages: list[Any]) -> ModelTurn:
        body = {
            "model": name,
            "messages": convert_to_openai_messages(messages),
            "tools": [tool_spec()],
            "tool_choice": {"type": "function", "function": {"name": "WorkflowEdit"}},
        }
        key = _hash(json.dumps(body, ensure_ascii=False, sort_keys=True))
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
    used = [node.agent_id for node in definition.nodes if node.type == "agent"]
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
            CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
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
