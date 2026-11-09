"""Workflow loops (workflow-loop-plan): a decision exit that leads back, bounded by rounds."""

from __future__ import annotations

import copy
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent
from app.workflows import agent_step, runner
from app.workflows.checks import graph_issues, loop_shape
from app.workflows.definition import WorkflowDefinition
from tests.conftest import seed_agent
from tests.test_workflow_runs import Crash, FakeAgent, _job
from tests.workflow_fixtures import ref, text

BASE = "/api/workflows"
AGENT = str(uuid.uuid4())


def _agent(node_id: str, prompt: list[Any], properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "agent",
        "agent_id": AGENT,
        "prompt": prompt,
        "output_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
        },
    }


def rewrite_until_passed(agent_id: str = AGENT, max_rounds: int = 3) -> dict[str, Any]:
    """start → draft → review → passed? true: done, false: back to draft, limit: stop."""
    raw = {
        "schema_version": "moldy.workflow.v1",
        "inputs": [{"name": "topic", "type": "string"}],
        "nodes": [
            {"id": "start", "type": "start"},
            _agent(
                "draft",
                [
                    text("주제: "),
                    ref("input", "/topic"),
                    text("\n검사 의견: "),
                    {"ref": {"node": "review", "path": "/feedback", "previous": True}},
                ],
                {"text": {"type": "string"}},
            ),
            _agent(
                "review",
                [text("검사: "), ref("draft", "/text")],
                {"passed": {"type": "boolean"}, "feedback": {"type": "string"}},
            ),
            {
                "id": "passed",
                "type": "decision_bool",
                "condition": {
                    "all": [{"source": "review", "path": "/passed", "op": "eq", "value": True}]
                },
                "repeat": {"max_rounds": max_rounds},
            },
            {"id": "done", "type": "output", "fields": {"text": [ref("draft", "/text")]}},
            {"id": "stop", "type": "fail", "reason": "검사를 통과하지 못했습니다"},
        ],
        "edges": [
            {"from": "start", "to": "draft"},
            {"from": "draft", "to": "review"},
            {"from": "review", "to": "passed"},
            {"from": "passed", "to": "done", "when": "true"},
            {"from": "passed", "to": "draft", "when": "false"},
            {"from": "passed", "to": "stop", "when": "limit"},
        ],
    }
    for node in raw["nodes"]:
        if node["type"] == "agent":
            node["agent_id"] = agent_id
    return raw


def _codes(raw: dict[str, Any]) -> list[str]:
    return sorted(issue.code for issue in graph_issues(WorkflowDefinition.model_validate(raw)))


def _node(raw: dict[str, Any], node_id: str) -> dict[str, Any]:
    return next(node for node in raw["nodes"] if node["id"] == node_id)


# --- publish checks -------------------------------------------------------------------


def test_a_bounded_loop_from_a_decision_is_clean() -> None:
    raw = rewrite_until_passed()
    assert _codes(raw) == []
    (loop,) = loop_shape(WorkflowDefinition.model_validate(raw)).loops
    assert (loop.decision, loop.when, loop.target) == ("passed", "false", "draft")
    assert loop.body == {"draft", "review", "passed"}


def test_the_repeat_limit_and_the_limit_exit_are_required() -> None:
    raw = rewrite_until_passed()
    del _node(raw, "passed")["repeat"]
    assert _codes(raw) == ["loop_limit_missing"]
    raw = rewrite_until_passed()
    raw["edges"] = [edge for edge in raw["edges"] if edge.get("when") != "limit"]
    assert _codes(raw) == ["loop_limit_exit_missing", "unreachable"]


def test_loop_settings_without_a_loop_are_refused() -> None:
    raw = rewrite_until_passed()
    raw["edges"] = [
        {"from": "passed", "to": "stop", "when": "false"} if edge.get("when") == "false" else edge
        for edge in raw["edges"]
    ]
    # Without the loop the draft's previous-round reference has no loop to read from either.
    assert _codes(raw) == ["loop_settings_without_loop", "ref_previous_scope"]


def test_only_a_decision_may_lead_back() -> None:
    raw = rewrite_until_passed()
    raw["edges"] = [edge for edge in raw["edges"] if edge["from"] != "review"]
    raw["edges"].append({"from": "review", "to": "draft"})
    assert "loop_from_not_decision" in _codes(raw)


def test_a_cycle_that_is_not_a_loop_is_still_refused() -> None:
    """``q → r`` and ``x → p → q``: r leads back to p, which does not run before r always."""
    raw = rewrite_until_passed()
    raw["nodes"] += [
        {
            "id": "x",
            "type": "decision_bool",
            "condition": {"all": [{"source": "input", "path": "/topic", "op": "exists"}]},
        },
        {"id": "p", "type": "transform", "template": [text("p")]},
        {"id": "q", "type": "transform", "template": [text("q")]},
        {
            "id": "r",
            "type": "decision_bool",
            "condition": {"all": [{"source": "input", "path": "/topic", "op": "exists"}]},
            "repeat": {"max_rounds": 2},
        },
    ]
    raw["edges"] = [
        {"from": "start", "to": "x"},
        {"from": "x", "to": "p", "when": "true"},
        {"from": "x", "to": "q", "when": "false"},
        {"from": "p", "to": "q"},
        {"from": "q", "to": "r"},
        {"from": "r", "to": "p", "when": "false"},
        {"from": "r", "to": "draft", "when": "true"},
        {"from": "r", "to": "stop", "when": "limit"},
        *raw["edges"][1:],
    ]
    assert "cycle" in _codes(raw)


def test_no_approval_output_or_fail_inside_a_loop() -> None:
    raw = rewrite_until_passed()
    raw["nodes"].append(
        {"id": "ok", "type": "approval", "title": [text("확인")], "timeout_minutes": 60}
    )
    raw["edges"] = [edge for edge in raw["edges"] if edge["from"] != "draft"]
    raw["edges"] += [
        {"from": "draft", "to": "ok"},
        {"from": "ok", "to": "review", "when": "approved"},
        {"from": "ok", "to": "stop", "when": "rejected"},
    ]
    codes = _codes(raw)
    assert "loop_body_kind" in codes and "loop_exit" in codes


def test_loops_may_not_overlap() -> None:
    raw = rewrite_until_passed()
    raw["nodes"].append(
        {
            "id": "again",
            "type": "decision_bool",
            "condition": {
                "all": [{"source": "review", "path": "/passed", "op": "eq", "value": True}]
            },
            "repeat": {"max_rounds": 2},
        }
    )
    raw["edges"] = [edge for edge in raw["edges"] if edge["from"] != "review"]
    raw["edges"] += [
        {"from": "review", "to": "again"},
        {"from": "again", "to": "passed", "when": "true"},
        {"from": "again", "to": "review", "when": "false"},
        {"from": "again", "to": "stop", "when": "limit"},
    ]
    assert "loop_overlap" in _codes(raw)


def test_previous_round_values_only_inside_the_loop_and_only_from_later_steps() -> None:
    raw = rewrite_until_passed()
    # Without ``previous`` the reviewer's feedback is not upstream of the draft.
    _node(raw, "draft")["prompt"][3] = ref("review", "/feedback")
    assert _codes(raw) == ["ref_not_upstream"]
    raw = rewrite_until_passed()
    _node(raw, "review")["prompt"][1] = {
        "ref": {"node": "draft", "path": "/text", "previous": True}
    }
    assert _codes(raw) == ["ref_previous_scope"]  # draft already ran in this round
    raw = rewrite_until_passed()
    _node(raw, "done")["fields"]["why"] = [
        {"ref": {"node": "review", "path": "/feedback", "previous": True}}
    ]
    assert _codes(raw) == ["ref_previous_scope"]  # done is outside the loop


def test_stored_definitions_keep_their_shape() -> None:
    """``previous`` and ``repeat`` are written only when set, so drafts without loops dump
    exactly as before."""
    dumped = WorkflowDefinition.model_validate(rewrite_until_passed()).dump()
    assert dumped["nodes"][2]["prompt"][1] == {"ref": {"node": "draft", "path": "/text"}}
    assert dumped["nodes"][1]["prompt"][3]["ref"]["previous"] is True
    plain = copy.deepcopy(rewrite_until_passed())
    del _node(plain, "passed")["repeat"]
    assert "repeat" not in WorkflowDefinition.model_validate(plain).dump()["nodes"][3]


# --- running ---------------------------------------------------------------------------


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeAgent:
    agent = FakeAgent()
    monkeypatch.setattr(agent_step, "execute_agent_invoke_structured", agent)
    return agent


async def _published_loop(client: AsyncClient, db: AsyncSession, **kwargs: Any) -> str:
    _, _, agent = await seed_agent(db)
    await db.commit()
    created = (await client.post(BASE, json={"name": "다시 쓰기"})).json()
    saved = await client.put(
        f"{BASE}/{created['id']}/draft",
        json={"revision": 1, "definition": rewrite_until_passed(str(agent.id), **kwargs)},
    )
    assert saved.status_code == 200, saved.text
    published = await client.post(
        f"{BASE}/{created['id']}/publish", json={"revision": saved.json()["draft_revision"]}
    )
    assert published.status_code == 201, published.text
    return created["id"]


async def _run(client: AsyncClient, workflow_id: str) -> str:
    response = await client.post(f"{BASE}/{workflow_id}/runs", json={"inputs": {"topic": "보고서"}})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _trail(detail: dict[str, Any]) -> list[tuple[str, int, str | None]]:
    return [(s["node_id"], s["round"], s["branch"]) for s in detail["steps"]]


@pytest.mark.asyncio
async def test_a_failed_review_runs_the_loop_again_with_the_previous_feedback(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent
) -> None:
    workflow_id = await _published_loop(client, db)
    fake.answers += [
        {"text": "초안 1"},
        {"passed": False, "feedback": "근거를 더하세요"},
        {"text": "초안 2"},
        {"passed": True, "feedback": "좋습니다"},
    ]
    run_id = await _run(client, workflow_id)
    assert await _job(run_id) == {"outcome": "completed"}

    detail = (await client.get(f"/api/workflow-runs/{run_id}")).json()
    assert _trail(detail) == [
        ("start", 1, None),
        ("draft", 1, None),
        ("review", 1, None),
        ("passed", 1, "false"),
        ("draft", 2, None),
        ("review", 2, None),
        ("passed", 2, "true"),
        ("done", 1, None),
    ]
    # Round 1 is kept; the output reads the last round.
    assert [s["output"] for s in detail["steps"] if s["node_id"] == "draft"] == [
        {"text": "초안 1"},
        {"text": "초안 2"},
    ]
    assert detail["output"] == {"text": "초안 2"}
    # The rewrite saw the review of the round before; the first draft saw nothing.
    drafts = [prompt for _, prompt in fake.calls[::2]]
    assert "<data></data>" in drafts[0] and "<data>근거를 더하세요</data>" in drafts[1]
    decision = next(s for s in detail["steps"] if s["node_id"] == "passed")
    assert decision["output"]["round"] == 1 and decision["output"]["max_rounds"] == 3


@pytest.mark.asyncio
async def test_the_limit_exit_after_the_last_round_is_audited(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent
) -> None:
    workflow_id = await _published_loop(client, db, max_rounds=2)
    fake.answers += [
        {"text": "초안 1"},
        {"passed": False, "feedback": "아직"},
        {"text": "초안 2"},
        {"passed": False, "feedback": "여전히"},
    ]
    run_id = await _run(client, workflow_id)
    assert await _job(run_id) == {"outcome": "failed"}
    detail = (await client.get(f"/api/workflow-runs/{run_id}")).json()
    assert _trail(detail)[-2:] == [("passed", 2, "limit"), ("stop", 1, None)]
    assert len(fake.calls) == 4  # two rounds, never a third
    last = detail["steps"][-2]["output"]
    assert (last["limit_reached"], last["reason"], last["round"]) == (True, "max_rounds", 2)
    (event,) = (
        await db.execute(sa.select(AuditEvent).where(AuditEvent.action == "workflow.loop_limit"))
    ).scalars()
    assert (event.target_type, event.target_id, event.outcome, event.reason_code) == (
        "workflow_run",
        run_id,
        "failure",
        "max_rounds",
    )
    assert event.event_metadata == {
        "workflow_id": workflow_id,
        "decision_node_id": "passed",
        "rounds": 2,
        "reason": "max_rounds",
    }


@pytest.mark.asyncio
async def test_no_new_round_starts_past_the_usage_limit(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow_id = await _published_loop(client, db)
    fake.answers += [{"text": "초안 1"}, {"passed": False, "feedback": "아직"}]

    async def limited(*_: Any) -> bool:
        return True

    monkeypatch.setattr(runner, "_usage_limited", limited)
    run_id = await _run(client, workflow_id)
    assert await _job(run_id) == {"outcome": "failed"}
    detail = (await client.get(f"/api/workflow-runs/{run_id}")).json()
    assert detail["steps"][-2]["output"]["reason"] == "usage_limit"
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_a_worker_dying_mid_round_resumes_that_round_once(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow_id = await _published_loop(client, db)
    fake.answers += [
        {"text": "초안 1"},
        {"passed": False, "feedback": "아직"},
        {"text": "초안 2"},
        {"passed": True, "feedback": "좋습니다"},
    ]
    run_id = await _run(client, workflow_id)
    real_decision = runner._decision
    seen = {"n": 0}

    async def dies_in_round_two(*args: Any) -> None:
        seen["n"] += 1
        if seen["n"] == 2:
            raise Crash  # round 2's review is stored, its decision is not
        await real_decision(*args)

    monkeypatch.setattr(runner, "_decision", dies_in_round_two)
    with pytest.raises(Crash):
        await _job(run_id)
    monkeypatch.setattr(runner, "_decision", real_decision)
    assert await _job(run_id) == {"outcome": "completed"}
    detail = (await client.get(f"/api/workflow-runs/{run_id}")).json()
    assert [(n, r) for n, r, _ in _trail(detail)].count(("review", 2)) == 1
    assert len(fake.calls) == 4  # nothing ran twice
