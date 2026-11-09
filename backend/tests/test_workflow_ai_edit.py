"""Workflow W5: AI editing proposes edit commands; nothing is saved until the person accepts."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from langchain_core.messages import AIMessage, ToolMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.token_usage import TokenUsage
from app.models.workflow import Workflow
from app.services.system_credential_resolver import SystemModelNotConfiguredError
from app.workflows import ai_edit
from app.workflows.ai_edit import (
    AddNode,
    Connect,
    RemoveNode,
    SetInputs,
    UpdateNode,
    apply_commands,
)
from app.workflows.definition import WorkflowDefinition, empty_definition
from tests.conftest import TEST_USER_ID, seed_agent
from tests.workflow_fixtures import inquiry_workflow, ref, text

BASE = "/api/workflows"


class FakeModel:
    """Answers each turn with the next prepared tool arguments; keeps what it was shown."""

    def __init__(self, *answers: dict[str, Any] | None) -> None:
        self.answers = list(answers)
        self.seen: list[list[Any]] = []

    async def __call__(self, messages: list[Any]) -> ai_edit.ModelTurn:
        self.seen.append(list(messages))
        args = self.answers.pop(0)
        call_id = f"call-{len(self.seen)}"
        raw = AIMessage(
            content="",
            tool_calls=[{"name": "WorkflowEdit", "args": args or {}, "id": call_id}],
        )
        return ai_edit.ModelTurn(args, raw, call_id, tokens_in=100, tokens_out=20)


@pytest.fixture(autouse=True)
def spend(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Spend rows go to the daily aggregate through their own sessions; keep what is sent."""
    sent: list[Any] = []

    async def fake_flush(_tenant_id: Any, entries: list[Any]) -> None:
        sent.extend(entries)

    monkeypatch.setattr("app.services.spend_flush.flush_tenant", fake_flush)
    return sent


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> list[FakeModel]:
    holder: list[FakeModel] = []

    async def system_edit_model(_context: Any) -> tuple[FakeModel, str]:
        return holder[0], "fake-edit-model"

    monkeypatch.setattr(ai_edit, "system_edit_model", system_edit_model)
    return holder


def agent_node(node_id: str, agent_id: str, prompt: list[Any] | None = None) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "agent",
        "label": "분류",
        "agent_id": agent_id,
        "prompt": prompt or [text("분류: "), ref("input", "/inquiry")],
        "output_schema": {
            "type": "object",
            "properties": {"category": {"type": "string"}},
            "required": ["category"],
        },
    }


def build(agent_id: str) -> dict[str, Any]:
    """start → classify → done, with one input."""
    return {
        "kind": "edit",
        "message": "문의를 분류해 결과로 내보냅니다.",
        "plan": ["입력 추가", "분류 단계", "출력"],
        "commands": [
            {"op": "set_inputs", "inputs": [{"name": "inquiry", "type": "string"}]},
            {"op": "add_node", "node": agent_node("classify", agent_id)},
            {
                "op": "add_node",
                "node": {
                    "id": "done",
                    "type": "output",
                    "label": "출력",
                    "fields": {"category": [ref("classify", "/category")]},
                },
            },
            {"op": "connect", "from": "start", "to": "classify"},
            {"op": "connect", "from": "classify", "to": "done"},
        ],
    }


async def _create(client: AsyncClient) -> dict[str, Any]:
    response = await client.post(BASE, json={"name": "문의 처리"})
    assert response.status_code == 201, response.text
    return response.json()


async def _assist(client: AsyncClient, workflow: dict[str, Any], message: str, **extra: Any) -> Any:
    body = {"revision": workflow["draft_revision"], "message": message, **extra}
    return await client.post(f"{BASE}/{workflow['id']}/assist", json=body)


@pytest.mark.asyncio
async def test_a_proposal_saves_nothing_until_it_is_accepted(
    client: AsyncClient, db: AsyncSession, model: list[FakeModel], spend: list[Any]
) -> None:
    _, _, agent = await seed_agent(db)
    await db.commit()
    workflow = await _create(client)
    model.append(FakeModel(build(str(agent.id))))

    response = await _assist(client, workflow, "문의를 분류해서 결과로 내보내 줘")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["kind"], body["issues"], body["base_revision"]) == ("proposal", [], 1)
    assert body["plan"] == ["입력 추가", "분류 단계", "출력"]
    assert [n["id"] for n in body["definition"]["nodes"]] == ["start", "classify", "done"]

    # Nothing written: the draft is still the empty one at revision 1.
    stored = await db.get(Workflow, uuid.UUID(workflow["id"]))
    assert stored is not None
    await db.refresh(stored)
    assert stored.draft_revision == 1
    assert [n["type"] for n in stored.draft_definition["nodes"]] == ["start"]

    # Accepting is an ordinary draft save from the revision the proposal was made on.
    saved = await client.put(
        f"{BASE}/{workflow['id']}/draft",
        json={"revision": body["base_revision"], "definition": body["definition"]},
    )
    assert saved.status_code == 200
    issues = (await client.get(f"{BASE}/{workflow['id']}/issues")).json()["issues"]
    assert issues == []

    # The model call is the person's usage.
    (usage,) = (await db.execute(sa.select(TokenUsage))).scalars().all()
    assert (usage.model_name, usage.prompt_tokens, usage.completion_tokens) == (
        "fake-edit-model",
        100,
        20,
    )
    assert (usage.caller_user_id, usage.invoker_type, usage.conversation_id) == (
        TEST_USER_ID,
        "user",
        None,
    )
    ((entry),) = spend
    assert (entry.user_id, entry.tokens_in, entry.tokens_out) == (TEST_USER_ID, 100, 20)


@pytest.mark.asyncio
async def test_the_model_sees_the_draft_and_runnable_agents_only(
    client: AsyncClient, db: AsyncSession, model: list[FakeModel]
) -> None:
    _, _, mine = await seed_agent(db, name="분류 담당", system_prompt="SECRET-PROMPT-TEXT")
    _, _, archived = await seed_agent(db, name="보관된 담당")
    archived.archived_at = datetime.now(UTC).replace(tzinfo=None)
    _, _, hidden = await seed_agent(db, name="숨은 담당", runtime_profile="skill_builder")
    _, _, theirs = await seed_agent(db, user_id=uuid.uuid4(), name="남의 담당")
    await db.commit()
    workflow = await _create(client)
    model.append(FakeModel({"kind": "question", "message": "어떤 문서를 다루나요?"}))

    history = [
        {"role": "user", "text": "흐름 하나 만들어 줘"},
        {"role": "assistant", "text": "어떤 일을 하나요?"},
    ]
    response = await _assist(client, workflow, "문서 처리", history=history)
    assert response.json() == {
        "kind": "question",
        "message": "어떤 문서를 다루나요?",
        "plan": [],
        "definition": None,
        "issues": [],
        "base_revision": 1,
    }
    (messages,) = model[0].seen
    assert [type(m).__name__ for m in messages] == [
        "SystemMessage",
        "HumanMessage",
        "AIMessage",
        "HumanMessage",
    ]
    payload = json.loads(messages[-1].content)
    assert set(payload) == {"draft", "agents", "request"}
    assert payload["agents"] == [
        {"id": str(mine.id), "name": "분류 담당", "description": mine.description or ""}
    ]
    shown = json.dumps([m.content for m in messages], ensure_ascii=False)
    assert "SECRET-PROMPT-TEXT" not in shown
    for other in (archived, hidden, theirs):
        assert str(other.id) not in shown


@pytest.mark.asyncio
async def test_an_edit_that_does_not_apply_goes_back_once_then_fails(
    client: AsyncClient, db: AsyncSession, model: list[FakeModel]
) -> None:
    await seed_agent(db)
    _, _, theirs = await seed_agent(db, user_id=uuid.uuid4())
    await db.commit()
    workflow = await _create(client)
    model.append(FakeModel(build(str(theirs.id)), build(str(theirs.id))))

    body = (await _assist(client, workflow, "분류 흐름")).json()
    assert (body["kind"], body["definition"]) == ("failed", None)
    first, second = model[0].seen
    note = second[-1]
    assert isinstance(note, ToolMessage) and note.tool_call_id == "call-1"
    assert f"agent_id {theirs.id} is not an offered agent" in note.content
    assert second[: len(first)] == first


@pytest.mark.asyncio
async def test_a_reply_that_is_not_the_tool_shape_is_retried(
    client: AsyncClient, db: AsyncSession, model: list[FakeModel]
) -> None:
    _, _, agent = await seed_agent(db)
    await db.commit()
    workflow = await _create(client)
    model.append(
        FakeModel({"kind": "edit", "commands": [{"op": "teleport"}]}, build(str(agent.id)))
    )

    body = (await _assist(client, workflow, "분류 흐름")).json()
    assert body["kind"] == "proposal"
    assert "message" in model[0].seen[1][-1].content


@pytest.mark.asyncio
async def test_checks_still_apply_to_a_proposal_that_keeps_an_issue(
    client: AsyncClient, db: AsyncSession, model: list[FakeModel]
) -> None:
    """A graph the checks reject is sent back once; if it stays, the issues are reported and
    the accepted draft still cannot be published."""
    _, _, agent = await seed_agent(db)
    await db.commit()
    workflow = await _create(client)
    dangling = build(str(agent.id))
    dangling["commands"] = dangling["commands"][:-1]  # classify leads nowhere
    model.append(FakeModel(dangling, dangling))

    body = (await _assist(client, workflow, "분류 흐름")).json()
    assert body["kind"] == "proposal"
    assert len(model[0].seen) == 2
    assert "check " in model[0].seen[1][-1].content
    assert body["issues"] and all(issue["code"] for issue in body["issues"])

    saved = await client.put(
        f"{BASE}/{workflow['id']}/draft",
        json={"revision": 1, "definition": body["definition"]},
    )
    publish = await client.post(
        f"{BASE}/{workflow['id']}/publish", json={"revision": saved.json()["draft_revision"]}
    )
    assert publish.status_code == 422


@pytest.mark.asyncio
async def test_a_stale_revision_or_a_missing_system_model_is_a_409(
    client: AsyncClient, db: AsyncSession, model: list[FakeModel], monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_agent(db)
    await db.commit()
    workflow = await _create(client)
    stale = await client.post(
        f"{BASE}/{workflow['id']}/assist", json={"revision": 7, "message": "아무거나"}
    )
    assert (stale.status_code, stale.json()["error"]["code"]) == (409, "WORKFLOW_DRAFT_STALE")

    async def missing(_context: Any) -> Any:
        raise SystemModelNotConfiguredError("text_primary")

    monkeypatch.setattr(ai_edit, "system_edit_model", missing)
    response = await _assist(client, workflow, "아무거나")
    assert (response.status_code, response.json()["error"]["code"]) == (
        409,
        "SYSTEM_LLM_NOT_CONFIGURED",
    )


def _q3(agent_id: str) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(inquiry_workflow(agent_id))


def test_commands_apply_in_order_and_name_what_failed() -> None:
    agent = str(uuid.uuid4())
    base = _q3(agent)
    result, problems = apply_commands(
        base,
        [
            RemoveNode(op="remove_node", node_id="review"),
            Connect.model_validate(
                {"op": "connect", "from": "is_urgent", "to": "draft", "when": "true"}
            ),
            UpdateNode.model_validate(
                {
                    "op": "update_node",
                    "node": {"id": "fail", "type": "fail", "label": "끝", "reason": "x"},
                }
            ),
            SetInputs(op="set_inputs", inputs=[]),
        ],
        {agent},
    )
    assert problems == []
    assert result is not None
    assert "review" not in {node.id for node in result.nodes}
    assert all("review" not in (edge.from_, edge.to) for edge in result.edges)
    assert result.inputs == []
    # The base draft is never touched.
    assert "review" in {node.id for node in base.nodes}


@pytest.mark.parametrize(
    ("command", "problem"),
    [
        (RemoveNode(op="remove_node", node_id="start"), "the start node cannot be removed"),
        (RemoveNode(op="remove_node", node_id="nope"), "no node 'nope'"),
        (
            AddNode.model_validate({"op": "add_node", "node": {"id": "fail", "type": "fail"}}),
            "node id 'fail' is already used",
        ),
        (
            AddNode.model_validate({"op": "add_node", "node": {"id": "other", "type": "start"}}),
            "the draft already has its start node",
        ),
        (
            UpdateNode.model_validate(
                {"op": "update_node", "node": {"id": "start", "type": "fail"}}
            ),
            "the start node keeps its kind",
        ),
        (
            Connect.model_validate({"op": "connect", "from": "start", "to": "ghost"}),
            "no node 'ghost'",
        ),
        (
            ai_edit.Disconnect.model_validate({"op": "disconnect", "from": "start", "to": "done"}),
            "no edge start -> done",
        ),
    ],
)
def test_commands_that_cannot_apply_are_refused(command: Any, problem: str) -> None:
    agent = str(uuid.uuid4())
    result, problems = apply_commands(_q3(agent), [command], {agent})
    assert result is None
    assert problems == [f"command 1 ({command.op}): {problem}"]


def test_a_result_that_breaks_the_schema_is_reported() -> None:
    base = WorkflowDefinition.model_validate(empty_definition())
    result, problems = apply_commands(
        base,
        [SetInputs.model_validate({"op": "set_inputs", "inputs": [{"name": "a"}, {"name": "a"}]})],
        set(),
    )
    assert result is None
    assert problems and problems[0].startswith("result: ")


def test_arguments_nested_under_one_unknown_key_are_unwrapped_once() -> None:
    """Claude through the gateway sometimes nests the whole call under one parameter name."""
    reply = {"kind": "question", "message": "무엇을 할까요?"}
    assert ai_edit.unwrapped({"$PARAMETER_NAME": reply}) == reply
    assert ai_edit.unwrapped(reply) == reply
    assert ai_edit.unwrapped({"kind": {"nested": True}}) == {"kind": {"nested": True}}
    assert ai_edit.unwrapped({"x": "text"}) == {"x": "text"}
    assert ai_edit.unwrapped(None) == {}
