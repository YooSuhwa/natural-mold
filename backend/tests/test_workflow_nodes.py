"""The remaining workflow nodes (workflow-nodes-plan): shape, checks, runs and recovery."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.agent_invoke_runner import StructuredInvokeResult
from app.agent_runtime.structured_output import StructuredOutcome
from app.config import settings
from app.models.message_attachment import MessageAttachment
from app.models.notification import Notification
from app.models.token_usage import TokenUsage
from app.models.workflow import WorkflowRun, WorkflowStepRun, WorkflowVersion
from app.workflows import agent_step, fanout, node_steps, runner
from app.workflows.checks import graph_issues
from app.workflows.definition import WorkflowDefinition
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, seed_agent
from tests.test_workflow_runs import Crash, FakeAgent, _job
from tests.workflow_fixtures import ref, text

BASE = "/api/workflows"
AGENT = "00000000-0000-0000-0000-00000000a001"
LIST_SCHEMA = {
    "type": "object",
    "properties": {"topics": {"type": "array", "items": {"type": "string"}}},
    "required": ["topics"],
}
SUMMARY = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}


def _graph(
    nodes: list[dict[str, Any]], edges: Sequence[tuple[str, str, str | None]], **extra: Any
) -> dict[str, Any]:
    return {
        "schema_version": "moldy.workflow.v1",
        "inputs": extra.get("inputs", [{"name": "topic", "type": "string"}]),
        "nodes": [{"id": "start", "type": "start"}, *nodes],
        "edges": [{"from": a, "to": b, **({"when": when} if when else {})} for a, b, when in edges],
    }


def _output(node: str = "done", **fields: list[Any]) -> dict[str, Any]:
    return {"id": node, "type": "output", "fields": fields or {"x": [text("끝")]}}


def _codes(raw: dict[str, Any]) -> list[str]:
    return [issue.code for issue in graph_issues(WorkflowDefinition.model_validate(raw))]


# --- shape and checks ----------------------------------------------------------------


def test_every_new_node_parses_and_round_trips() -> None:
    raw = _graph(
        [
            {
                "id": "lister",
                "type": "agent",
                "agent_id": AGENT,
                "prompt": [ref("input", "/topic")],
                "output_schema": LIST_SCHEMA,
            },
            {
                "id": "each",
                "type": "for_each",
                "source": "lister",
                "path": "/topics",
                "agent_id": AGENT,
                "prompt": [text("조사: "), ref("item")],
                "output_schema": SUMMARY,
                "max_items": 5,
            },
            {
                "id": "both",
                "type": "parallel",
                "branches": [
                    {
                        "name": "pro",
                        "agent_id": AGENT,
                        "prompt": [text("찬성")],
                        "output_schema": SUMMARY,
                    },
                    {
                        "name": "con",
                        "agent_id": AGENT,
                        "prompt": [text("반대")],
                        "output_schema": SUMMARY,
                    },
                ],
            },
            {"id": "now", "type": "tool", "tool": "current_datetime"},
            {
                "id": "sum",
                "type": "llm",
                "prompt": [ref("each", "/items")],
                "output_schema": SUMMARY,
            },
            {
                "id": "ok",
                "type": "decision_bool",
                "evaluator": "llm",
                "question": [text("충분한가? "), ref("sum", "/summary")],
            },
            {
                "id": "route",
                "type": "switch",
                "source": "input",
                "path": "/topic",
                "cases": [{"name": "news", "value": "뉴스"}],
            },
            {"id": "ask", "type": "ask", "question": [text("더 볼까요?")], "timeout_minutes": 60},
            {"id": "ping", "type": "notify", "title": [ref("ask", "/answer")]},
            _output(
                summary=[ref("sum", "/summary")],
                pro=[ref("both", "/pro/summary")],
                count=[ref("each", "/count")],
                case=[ref("route", "/case")],
                reason=[ref("ok", "/reason")],
                time=[ref("now", "/result")],
            ),
            {"id": "stop", "type": "fail"},
        ],
        [
            ("start", "lister", None),
            ("lister", "each", None),
            ("each", "both", None),
            ("both", "now", None),
            ("now", "sum", None),
            ("sum", "ok", None),
            ("ok", "route", "true"),
            ("ok", "stop", "false"),
            ("route", "ask", "news"),
            ("route", "stop", "default"),
            ("ask", "ping", "answered"),
            ("ask", "stop", "timeout"),
            ("ping", "done", None),
        ],
    )
    definition = WorkflowDefinition.model_validate(raw)
    assert graph_issues(definition) == []
    again = WorkflowDefinition.model_validate(definition.dump())
    assert again.dump() == definition.dump()
    # A rule decision keeps its stored shape (no evaluator/question keys added).
    rule = WorkflowDefinition.model_validate(
        _graph(
            [
                {
                    "id": "d",
                    "type": "decision_bool",
                    "condition": {"all": [{"source": "input", "path": "/topic", "op": "exists"}]},
                },
                _output(),
            ],
            [("start", "d", None), ("d", "done", "true"), ("d", "done", "false")],
        )
    )
    assert set(rule.dump()["nodes"][1]) == {"id", "type", "label", "condition"}


@pytest.mark.parametrize(
    "node",
    [
        {"id": "d", "type": "decision_bool", "evaluator": "llm"},  # no question
        {
            "id": "d",
            "type": "decision_bool",
            "evaluator": "llm",
            "question": [text("?")],
            "condition": {"all": [{"source": "input", "op": "exists"}]},
        },
        {
            "id": "s",
            "type": "switch",
            "source": "input",
            "cases": [{"name": "default", "value": 1}],
        },
        {
            "id": "s",
            "type": "switch",
            "source": "input",
            "cases": [{"name": "a", "value": 1}, {"name": "a", "value": 2}],
        },
        {"id": "t", "type": "tool", "tool": "send_mail"},
        {"id": "item", "type": "fail"},
        {
            "id": "f",
            "type": "for_each",
            "source": "x",
            "agent_id": AGENT,
            "prompt": [],
            "output_schema": SUMMARY,
            "max_items": 21,
        },
    ],
)
def test_the_shape_refuses(node: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(_graph([node], []))


def test_switch_needs_every_case_and_default_wired() -> None:
    switch = {
        "id": "s",
        "type": "switch",
        "source": "input",
        "path": "/topic",
        "cases": [{"name": "a", "value": "a"}, {"name": "b", "value": "b"}],
    }
    raw = _graph([switch, _output()], [("start", "s", None), ("s", "done", "a")])
    assert "branch_missing" in _codes(raw)
    raw["edges"] += [{"from": "s", "to": "done", "when": "b"}]
    raw["edges"] += [{"from": "s", "to": "done", "when": "default"}]
    assert _codes(raw) == []


def test_ask_needs_a_time_limit_both_exits_and_stays_out_of_loops() -> None:
    ask = {"id": "ask", "type": "ask", "question": [text("?")]}
    raw = _graph([ask, _output()], [("start", "ask", None), ("ask", "done", "answered")])
    assert {"ask_no_timeout", "branch_missing"} <= set(_codes(raw))
    looped = _graph(
        [
            {**ask, "timeout_minutes": 5},
            {
                "id": "again",
                "type": "decision_bool",
                "condition": {
                    "all": [{"source": "ask", "path": "/answer", "op": "eq", "value": "네"}]
                },
                "repeat": {"max_rounds": 3},
            },
            _output(),
        ],
        [
            ("start", "ask", None),
            ("ask", "again", "answered"),
            ("ask", "done", "timeout"),
            ("again", "ask", "true"),
            ("again", "done", "false"),
            ("again", "done", "limit"),
        ],
    )
    assert "loop_body_kind" in _codes(looped)


def test_for_each_needs_a_list_and_item_stays_inside_it() -> None:
    agent = {
        "id": "a",
        "type": "agent",
        "agent_id": AGENT,
        "prompt": [text("x")],
        "output_schema": LIST_SCHEMA,
    }
    each = {
        "id": "each",
        "type": "for_each",
        "source": "a",
        "path": "/topics",
        "agent_id": AGENT,
        "prompt": [ref("item")],
        "output_schema": SUMMARY,
    }
    edges = [("start", "a", None), ("a", "each", None), ("each", "done", None)]
    assert _codes(_graph([agent, each, _output(n=[ref("each", "/items/0/summary")])], edges)) == []
    not_list = {**each, "path": ""}
    assert "for_each_not_list" in _codes(_graph([agent, not_list, _output()], edges))
    from_input = {**each, "source": "input", "path": "/topic"}
    assert "for_each_not_list" in _codes(_graph([agent, from_input, _output()], edges))
    stray = _output(n=[ref("item")])
    assert "ref_item_scope" in _codes(_graph([agent, each, stray], edges))


def test_parallel_and_tool_fields_are_checked() -> None:
    one = {
        "id": "p",
        "type": "parallel",
        "branches": [
            {"name": "a", "agent_id": AGENT, "prompt": [text("x")], "output_schema": SUMMARY}
        ],
    }
    edges = [("start", "p", None), ("p", "done", None)]
    assert "parallel_branches" in _codes(_graph([one, _output()], edges))
    tool = {"id": "p", "type": "tool", "tool": "web_search", "args": {"url": [text("x")]}}
    assert "tool_args" in _codes(_graph([tool, _output()], edges))
    ok = {
        "id": "p",
        "type": "tool",
        "tool": "web_search",
        "args": {"query": [ref("input", "/topic")]},
    }
    assert _codes(_graph([ok, _output(r=[ref("p", "/result")])], edges)) == []
    assert "ref_bad_path" in _codes(_graph([ok, _output(r=[ref("p", "/text")])], edges))


# --- runs ------------------------------------------------------------------------------


class FakeModel:
    """The system model: answers in order; records each prompt."""

    def __init__(self, *answers: dict[str, Any] | None) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    async def __call__(self, messages: list[Any], schema: dict[str, Any]) -> node_steps.ModelReply:
        self.prompts.append(str(messages[1].content))
        return node_steps.ModelReply(
            args=self.answers.pop(0), raw=None, tool_call_id=None, tokens_in=10, tokens_out=5
        )


class ByPrompt(FakeAgent):
    """An agent fake that answers from the prompt (children run concurrently)."""

    def __init__(self, answer: Any) -> None:
        super().__init__()
        self.answer = answer

    async def __call__(self, cfg: Any, messages: list[dict[str, Any]], **_: Any) -> Any:
        self.calls.append((cfg, messages[0]["content"]))
        value = self.answer(messages[0]["content"])
        if isinstance(value, StructuredOutcome):
            return StructuredInvokeResult("", value)
        return StructuredInvokeResult("", StructuredOutcome("ok", value))


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> FakeModel:
    fake = FakeModel()

    async def system_model(_context: Any) -> tuple[Any, str]:
        return fake, "fake-model"

    async def no_flush(*_: Any, **__: Any) -> None:
        return None

    monkeypatch.setattr(node_steps, "system_model", system_model)
    monkeypatch.setattr("app.services.spend_flush.flush_tenant", no_flush)
    return fake


async def _publish(client: AsyncClient, definition: dict[str, Any]) -> str:
    created = (await client.post(BASE, json={"name": "노드 시험"})).json()
    saved = await client.put(
        f"{BASE}/{created['id']}/draft", json={"revision": 1, "definition": definition}
    )
    assert saved.status_code == 200, saved.text
    published = await client.post(
        f"{BASE}/{created['id']}/publish", json={"revision": saved.json()["draft_revision"]}
    )
    assert published.status_code == 201, published.text
    return str(created["id"])


async def _run(client: AsyncClient, workflow_id: str, **inputs: Any) -> str:
    response = await client.post(f"{BASE}/{workflow_id}/runs", json={"inputs": inputs})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def _detail(client: AsyncClient, run_id: str) -> dict[str, Any]:
    return (await client.get(f"/api/workflow-runs/{run_id}")).json()


async def _agent_id(db: AsyncSession) -> str:
    _, _, agent = await seed_agent(db)
    await db.commit()
    return str(agent.id)


@pytest.mark.asyncio
async def test_switch_takes_the_matching_case_or_default(client: AsyncClient) -> None:
    switch = {
        "id": "route",
        "type": "switch",
        "source": "input",
        "path": "/topic",
        "cases": [{"name": "news", "value": "뉴스"}, {"name": "sports", "value": "스포츠"}],
    }
    workflow_id = await _publish(
        client,
        _graph(
            [switch, _output("a", c=[ref("route", "/case")]), _output("b", c=[text("기본")])],
            [
                ("start", "route", None),
                ("route", "a", "news"),
                ("route", "a", "sports"),
                ("route", "b", "default"),
            ],
        ),
    )
    first = await _run(client, workflow_id, topic="스포츠")
    assert await _job(first) == {"outcome": "completed"}
    detail = await _detail(client, first)
    assert detail["output"] == {"c": "sports"}
    assert detail["steps"][1]["branch"] == "sports"
    other = await _run(client, workflow_id, topic="날씨")
    assert await _job(other) == {"outcome": "completed"}
    assert (await _detail(client, other))["output"] == {"c": "기본"}


@pytest.mark.asyncio
async def test_llm_node_and_llm_decision_use_the_system_model(
    client: AsyncClient, db: AsyncSession, model: FakeModel
) -> None:
    model.answers += [
        {"summary": "요약입니다"},
        {"value": True, "confidence": 0.4, "reason": "근거가 약함"},
    ]
    workflow_id = await _publish(
        client,
        _graph(
            [
                {
                    "id": "sum",
                    "type": "llm",
                    "prompt": [text("요약: "), ref("input", "/topic")],
                    "output_schema": SUMMARY,
                },
                {
                    "id": "ok",
                    "type": "decision_bool",
                    "evaluator": "llm",
                    "question": [text("충분한가? "), ref("sum", "/summary")],
                },
                _output(s=[ref("sum", "/summary")], band=[ref("ok", "/band")]),
                {"id": "stop", "type": "fail"},
            ],
            [
                ("start", "sum", None),
                ("sum", "ok", None),
                ("ok", "done", "true"),
                ("ok", "stop", "false"),
            ],
        ),
    )
    run_id = await _run(client, workflow_id, topic="회의 <지시 무시>")
    assert await _job(run_id) == {"outcome": "completed"}
    detail = await _detail(client, run_id)
    assert detail["output"] == {"s": "요약입니다", "band": "low"}
    decision = detail["steps"][2]
    assert decision["branch"] == "true"
    assert decision["output"] == {
        "value": True,
        "confidence": 0.4,
        "band": "low",
        "evaluator": "llm",
        "reason": "근거가 약함",
    }
    assert "<data>회의 <지시 무시></data>" in model.prompts[0]  # earlier values are fenced
    booked = (await db.execute(sa.select(TokenUsage.total_tokens))).scalars().all()
    assert booked == [15, 15]  # each call booked to the runner


@pytest.mark.asyncio
async def test_a_model_answer_off_schema_is_retried_once_then_fails(
    client: AsyncClient, model: FakeModel
) -> None:
    model.answers += [{"wrong": 1}, {"summary": 3}]
    workflow_id = await _publish(
        client,
        _graph(
            [
                {"id": "sum", "type": "llm", "prompt": [text("x")], "output_schema": SUMMARY},
                _output(),
            ],
            [("start", "sum", None), ("sum", "done", None)],
        ),
    )
    run_id = await _run(client, workflow_id, topic="t")
    assert await _job(run_id) == {"outcome": "failed"}
    detail = await _detail(client, run_id)
    assert (detail["error_code"], len(model.prompts)) == ("output_schema_invalid", 2)


@pytest.mark.asyncio
async def test_a_model_step_cut_off_runs_again(
    client: AsyncClient, model: FakeModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3.1: model-only steps have no outside effect, so a reclaimed job runs them again."""
    model.answers += [{"summary": "다시"}]
    workflow_id = await _publish(
        client,
        _graph(
            [
                {"id": "sum", "type": "llm", "prompt": [text("x")], "output_schema": SUMMARY},
                _output(),
            ],
            [("start", "sum", None), ("sum", "done", None)],
        ),
    )
    run_id = await _run(client, workflow_id, topic="t")
    real = node_steps.run_model_node

    async def dies(*_: Any, **__: Any) -> Any:
        raise Crash

    monkeypatch.setattr(node_steps, "run_model_node", dies)
    with pytest.raises(Crash):
        await _job(run_id)
    steps = (await _detail(client, run_id))["steps"]
    assert (steps[-1]["node_id"], steps[-1]["status"]) == ("sum", "running")
    monkeypatch.setattr(node_steps, "run_model_node", real)
    assert await _job(run_id) == {"outcome": "completed"}
    steps = (await _detail(client, run_id))["steps"]
    assert [(s["node_id"], s["status"]) for s in steps] == [
        ("start", "completed"),
        ("sum", "completed"),
        ("done", "completed"),
    ]


@pytest.mark.asyncio
async def test_a_model_step_past_the_usage_limit_does_not_start(
    client: AsyncClient, model: FakeModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow_id = await _publish(
        client,
        _graph(
            [
                {"id": "sum", "type": "llm", "prompt": [text("x")], "output_schema": SUMMARY},
                _output(),
            ],
            [("start", "sum", None), ("sum", "done", None)],
        ),
    )

    async def limited(*_: Any) -> bool:
        return True

    monkeypatch.setattr(runner, "_usage_limited", limited)
    run_id = await _run(client, workflow_id, topic="t")
    assert await _job(run_id) == {"outcome": "failed"}
    assert (await _detail(client, run_id))["error_code"] == "usage_limit"
    assert model.prompts == []


def _ask_workflow() -> dict[str, Any]:
    return _graph(
        [
            {
                "id": "ask",
                "type": "ask",
                "label": "확인 질문",
                "question": [text("어느 지역인가요? "), ref("input", "/topic")],
                "timeout_minutes": 60,
            },
            _output(answer=[ref("ask", "/answer")]),
            {"id": "late", "type": "fail", "reason": "답이 없었습니다"},
        ],
        [("start", "ask", None), ("ask", "done", "answered"), ("ask", "late", "timeout")],
    )


async def _asked(client: AsyncClient) -> tuple[str, dict[str, Any]]:
    run_id = await _run(client, await _publish(client, _ask_workflow()), topic="날씨")
    assert await _job(run_id) == {"outcome": "waiting"}
    (request,) = (await client.get("/api/approvals")).json()["items"]
    action = request["actions"][0]
    assert (request["kind"], action["name"], action["allowed_decisions"]) == (
        "business",
        "workflow_ask",
        ["approve"],
    )
    assert action["args"]["body"] == "어느 지역인가요? 날씨"
    return run_id, request


@pytest.mark.asyncio
async def test_ask_takes_the_written_answer(client: AsyncClient) -> None:
    run_id, request = await _asked(client)
    url = f"/api/approvals/{request['id']}/decision"
    empty = await client.post(url, json={"decisions": [{"type": "approve", "message": "  "}]})
    assert (empty.status_code, empty.json()["error"]["code"]) == (422, "answer_required")
    reject = await client.post(url, json={"decisions": [{"type": "reject"}]})
    assert reject.status_code == 422  # only an answer is allowed
    answered = await client.post(url, json={"decisions": [{"type": "approve", "message": "서울"}]})
    assert answered.status_code == 200, answered.text
    assert await _job(run_id) == {"outcome": "completed"}
    detail = await _detail(client, run_id)
    assert detail["output"] == {"answer": "서울"}
    assert detail["steps"][1]["branch"] == "answered"


@pytest.mark.asyncio
async def test_an_unanswered_ask_takes_the_timeout_exit(
    client: AsyncClient, db: AsyncSession
) -> None:
    from app.approvals import inbox
    from app.models.approval_request import ApprovalRequest

    run_id, request = await _asked(client)
    row = await db.get(ApprovalRequest, uuid.UUID(request["id"]))
    assert row is not None
    row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db.commit()
    assert await inbox.expire_due() == 1
    assert await _job(run_id) == {"outcome": "failed"}
    detail = await _detail(client, run_id)
    assert detail["steps"][1]["branch"] == "timeout"
    assert detail["steps"][1]["output"] == {"answer": None}
    assert detail["error_message"] == "답이 없었습니다"


@pytest.mark.asyncio
async def test_notify_sends_one_notice_to_the_runner(client: AsyncClient, db: AsyncSession) -> None:
    notify = {
        "id": "ping",
        "type": "notify",
        "title": [text("완료: "), ref("input", "/topic")],
        "body": [text("결과를 확인하세요")],
    }
    workflow_id = await _publish(
        client,
        _graph(
            [notify, {**notify, "id": "ping2"}, _output(sent=[ref("ping", "/sent")])],
            [("start", "ping", None), ("ping", "ping2", None), ("ping2", "done", None)],
        ),
    )
    run_id = await _run(client, workflow_id, topic="보고서")
    assert await _job(run_id) == {"outcome": "completed"}
    assert (await _detail(client, run_id))["output"] == {"sent": True}
    rows = (
        (await db.execute(sa.select(Notification).where(Notification.kind == "workflow.notify")))
        .scalars()
        .all()
    )
    assert len(rows) == 2  # one per notify step, never merged
    assert all(row.user_id == TEST_USER_ID for row in rows)
    assert rows[0].payload["title"] == "완료: 보고서"
    listed = (await client.get("/api/notifications")).json()["items"]
    notice = next(item for item in listed if item["kind"] == "workflow.notify")
    assert notice["target"]["accessible"] is True
    assert notice["link"] == f"/workflows/{workflow_id}/runs/{run_id}"


def _fanout_graph(agent_id: str) -> dict[str, Any]:
    return _graph(
        [
            {
                "id": "lister",
                "type": "agent",
                "agent_id": agent_id,
                "prompt": [text("목록: "), ref("input", "/topic")],
                "output_schema": LIST_SCHEMA,
            },
            {
                "id": "each",
                "type": "for_each",
                "source": "lister",
                "path": "/topics",
                "agent_id": agent_id,
                "prompt": [text("조사: "), ref("item")],
                "output_schema": SUMMARY,
                "max_items": 4,
            },
            {
                "id": "views",
                "type": "parallel",
                "branches": [
                    {
                        "name": name,
                        "agent_id": agent_id,
                        "prompt": [text(f"관점 {name}: "), ref("each", "/count")],
                        "output_schema": SUMMARY,
                    }
                    for name in ("pro", "con")
                ],
            },
            _output(
                items=[ref("each", "/items")],
                pro=[ref("views", "/pro/summary")],
                con=[ref("views", "/con/summary")],
            ),
        ],
        [
            ("start", "lister", None),
            ("lister", "each", None),
            ("each", "views", None),
            ("views", "done", None),
        ],
    )


def _fanout_agent(topics: list[str], fail_on: str | None = None) -> ByPrompt:
    def answer(prompt: str) -> Any:
        prompt = prompt.removeprefix(agent_step_note())
        if prompt.startswith("목록"):
            return {"topics": topics}
        if fail_on and fail_on in prompt:
            return StructuredOutcome("schema_invalid")
        return {"summary": prompt[:40]}

    return ByPrompt(answer)


def agent_step_note() -> str:
    from app.workflows.values import DATA_NOTE

    return DATA_NOTE


@pytest.mark.asyncio
async def test_for_each_and_parallel_run_children_and_merge(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = await _agent_id(db)
    fake = _fanout_agent(["가", "나", "다"])
    monkeypatch.setattr(agent_step, "execute_agent_invoke_structured", fake)
    workflow_id = await _publish(client, _fanout_graph(agent_id))
    version = await db.scalar(sa.select(WorkflowVersion))
    assert version is not None
    assert sorted(version.agent_pins) == ["each", "lister", "views.con", "views.pro"]
    run_id = await _run(client, workflow_id, topic="주제")
    assert await _job(run_id) == {"outcome": "completed"}
    detail = await _detail(client, run_id)
    assert [item["summary"] for item in detail["output"]["items"]] == [
        "조사: <data>가</data>",
        "조사: <data>나</data>",
        "조사: <data>다</data>",
    ]
    assert detail["output"]["pro"] == "관점 pro: <data>3</data>"
    main = [s for s in detail["steps"] if s["parent_step_id"] is None]
    assert [s["node_id"] for s in main] == ["start", "lister", "each", "views", "done"]
    each = next(s for s in main if s["node_id"] == "each")
    kids = [s for s in detail["steps"] if s["parent_step_id"] == each["id"]]
    assert [(s["node_id"], s["branch_key"], s["status"]) for s in kids] == [
        ("each.0", "0", "completed"),
        ("each.1", "1", "completed"),
        ("each.2", "2", "completed"),
    ]
    assert all(s["conversation_id"] for s in kids)  # each child is its own agent run
    assert len(fake.calls) == 1 + 3 + 2
    # Children refuse a tool that needs approval (no single point to resume from).
    child_cfgs = [cfg for cfg, prompt in fake.calls if "목록" not in prompt]
    assert {cfg.unattended_approval for cfg in child_cfgs} == {"block"}


@pytest.mark.asyncio
async def test_a_failing_child_ends_the_step_and_the_run(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = await _agent_id(db)
    monkeypatch.setattr(
        agent_step, "execute_agent_invoke_structured", _fanout_agent(["가", "나"], fail_on="나")
    )
    run_id = await _run(client, await _publish(client, _fanout_graph(agent_id)), topic="주제")
    assert await _job(run_id) == {"outcome": "failed"}
    detail = await _detail(client, run_id)
    assert detail["error_code"] == "output_schema_invalid"
    assert detail["steps"][-1]["status"] == "failed"  # the last child; its parent failed too


@pytest.mark.asyncio
async def test_for_each_over_nothing_or_too_much(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = await _agent_id(db)
    monkeypatch.setattr(agent_step, "execute_agent_invoke_structured", _fanout_agent([]))
    workflow_id = await _publish(client, _fanout_graph(agent_id))
    empty = await _run(client, workflow_id, topic="t")
    assert await _job(empty) == {"outcome": "completed"}
    assert (await _detail(client, empty))["output"]["items"] == []
    monkeypatch.setattr(
        agent_step, "execute_agent_invoke_structured", _fanout_agent(["a", "b", "c", "d", "e"])
    )
    many = await _run(client, workflow_id, topic="t")
    assert await _job(many) == {"outcome": "failed"}
    assert (await _detail(client, many))["error_code"] == "for_each_too_many"


@pytest.mark.asyncio
async def test_a_child_cut_off_after_acceptance_is_asked_about_not_run_again(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = await _agent_id(db)
    fake = _fanout_agent(["가", "나"])
    monkeypatch.setattr(agent_step, "execute_agent_invoke_structured", fake)
    real_execute = agent_step.execute

    async def dies_on_first_child(context: Any, step: Any, prepared: Any) -> Any:
        if step.node_id == "each.0":
            raise Crash
        return await real_execute(context, step, prepared)

    monkeypatch.setattr(agent_step, "execute", dies_on_first_child)
    monkeypatch.setattr(fanout, "FOR_EACH_BATCH", 1)  # only the first child is in flight
    run_id = await _run(client, await _publish(client, _fanout_graph(agent_id)), topic="t")
    with pytest.raises(Crash):
        await _job(run_id)
    monkeypatch.setattr(agent_step, "execute", real_execute)
    assert await _job(run_id) == {"outcome": "unknown"}
    (request,) = (await client.get("/api/approvals")).json()["items"]
    assert request["actions"][0]["name"] == "workflow_unknown_check"
    calls = len(fake.calls)
    decided = await client.post(
        f"/api/approvals/{request['id']}/decision", json={"decisions": [{"type": "approve"}]}
    )
    assert decided.status_code == 200, decided.text
    assert await _job(run_id) == {"outcome": "completed"}
    # The unknown child ran again (in a fresh conversation), then the rest, once each.
    assert len(fake.calls) - calls == 2 + 2
    child = await db.scalar(sa.select(WorkflowStepRun).where(WorkflowStepRun.node_id == "each.0"))
    assert child is not None
    await db.refresh(child)
    assert child.input is not None and len(child.input["previous_attempts"]) == 1


@pytest.mark.asyncio
async def test_a_tool_step_returns_the_tool_text(client: AsyncClient) -> None:
    workflow_id = await _publish(
        client,
        _graph(
            [
                {"id": "now", "type": "tool", "tool": "current_datetime"},
                _output(t=[ref("now", "/result")]),
            ],
            [("start", "now", None), ("now", "done", None)],
        ),
    )
    run_id = await _run(client, workflow_id, topic="t")
    assert await _job(run_id) == {"outcome": "completed"}
    assert "date" in (await _detail(client, run_id))["output"]["t"]


@pytest.mark.asyncio
async def test_a_file_input_is_attached_to_the_agent_step_that_uses_it(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    blob = tmp_path / "report.txt"
    blob.write_text("분기 매출 보고")
    upload = MessageAttachment(
        user_id=TEST_USER_ID,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        filename="보고서.txt",
        mime_type="text/plain",
        size_bytes=blob.stat().st_size,
        storage_path=str(blob),
        url="/api/uploads/x",
    )
    db.add(upload)
    agent_id = await _agent_id(db)
    await db.refresh(upload)
    fake = FakeAgent({"summary": "요약"})
    monkeypatch.setattr(agent_step, "execute_agent_invoke_structured", fake)
    workflow_id = await _publish(
        client,
        _graph(
            [
                {
                    "id": "read",
                    "type": "agent",
                    "agent_id": agent_id,
                    "prompt": [text("이 파일을 요약: "), ref("input", "/doc")],
                    "output_schema": SUMMARY,
                },
                _output(name=[ref("input", "/doc")]),
            ],
            [("start", "read", None), ("read", "done", None)],
            inputs=[{"name": "doc", "type": "file", "label": "보고서"}],
        ),
    )
    missing = await client.post(
        f"{BASE}/{workflow_id}/runs", json={"inputs": {"doc": str(uuid.uuid4())}}
    )
    assert missing.status_code == 422
    run_id = await _run(client, workflow_id, doc=str(upload.id))
    assert await _job(run_id) == {"outcome": "completed"}
    detail = await _detail(client, run_id)
    assert detail["output"] == {"name": "보고서.txt"}  # templates see the file name
    assert "<data>보고서.txt</data>" in fake.calls[0][1]
    run = await db.get(WorkflowRun, uuid.UUID(run_id))
    assert run is not None and run.files == {
        "doc": {"id": str(upload.id), "filename": "보고서.txt"}
    }
    await db.refresh(upload)
    assert upload.message_id == f"workflow-run:{run_id}"  # kept from the unsent sweep
    step = await db.scalar(sa.select(WorkflowStepRun).where(WorkflowStepRun.node_id == "read"))
    assert step is not None
    copies = (
        (
            await db.execute(
                sa.select(MessageAttachment).where(
                    MessageAttachment.conversation_id == step.conversation_id
                )
            )
        )
        .scalars()
        .all()
    )
    copied = [(c.filename, Path(c.storage_path).read_text()) for c in copies]  # noqa: ASYNC240
    assert copied == [("보고서.txt", "분기 매출 보고")]
    cfg = fake.calls[0][0]
    assert cfg.attachment_inputs is not None
    again = await client.post(
        f"{BASE}/{workflow_id}/runs", json={"inputs": {"doc": str(upload.id)}}
    )
    assert again.status_code == 422  # an upload is used by one run
