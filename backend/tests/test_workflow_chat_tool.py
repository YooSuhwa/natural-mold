"""Workflows as chat tools (workflow-tool-plan): links, tool assembly, wait and resume."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, TypedDict

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_config import AgentConfig
from app.models.audit_event import AuditEvent
from app.models.workflow import Workflow, WorkflowRun
from app.workflows import chat_resume, chat_tool
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID, seed_agent
from tests.sqlite_test_engine import TestSession
from tests.workflow_fixtures import inquiry_workflow

BASE = "/api/workflows"


@pytest.fixture(autouse=True)
def sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tool and the resume sweep open their own sessions (call-time imports)."""
    monkeypatch.setattr("app.database.async_session", TestSession)


async def _published(client: AsyncClient, db: AsyncSession, agent_id: str) -> dict[str, Any]:
    created = (await client.post(BASE, json={"name": "문의 처리"})).json()
    saved = (
        await client.put(
            f"{BASE}/{created['id']}/draft",
            json={"revision": 1, "definition": inquiry_workflow(agent_id)},
        )
    ).json()
    published = await client.post(
        f"{BASE}/{created['id']}/publish", json={"revision": saved["draft_revision"]}
    )
    assert published.status_code == 201, published.text
    return created


def _cfg(agent_id: uuid.UUID, conversation_id: uuid.UUID, run_path: str = "chat") -> AgentConfig:
    return AgentConfig(
        provider="openai",
        model_name="gpt-4o",
        api_key=None,
        base_url=None,
        system_prompt="x",
        tools_config=[],
        thread_id=str(conversation_id),
        org_context=unit_context(TEST_USER_ID),
        agent_id=str(agent_id),
        user_id=str(TEST_USER_ID),
        run_path=run_path,
    )


async def _cfg_with_tools(
    db: AsyncSession, agent_id: uuid.UUID, conversation_id: uuid.UUID, run_path: str = "chat"
) -> AgentConfig:
    cfg = _cfg(agent_id, conversation_id, run_path)
    cfg.workflow_tools = await chat_tool.specs_for_agent(db, unit_context(TEST_USER_ID), agent_id)
    return cfg


# --- links -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_my_published_workflows_can_be_linked_and_breaks_show(
    client: AsyncClient, db: AsyncSession
) -> None:
    _, _, agent = await seed_agent(db)
    await db.commit()
    workflow = await _published(client, db, str(agent.id))
    draft_only = (await client.post(BASE, json={"name": "초안만"})).json()
    theirs = Workflow(
        user_id=uuid.uuid4(),
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        name="남의 워크플로",
        draft_definition={"nodes": []},
        draft_revision=1,
        published_version_no=1,
    )
    db.add(theirs)
    await db.commit()
    path = f"/api/agents/{agent.id}/workflow-tools"

    for refused, reason in ((draft_only["id"], "workflow_not_published"), (str(theirs.id), None)):
        response = await client.put(path, json={"workflow_ids": [refused]})
        assert response.status_code == 422, response.text
        body = response.json()["error"]
        assert body["code"] == "WORKFLOW_TOOL_UNAVAILABLE"
        assert body["details"]["reason"] == (reason or "workflow_unavailable")

    linked = await client.put(path, json={"workflow_ids": [workflow["id"]]})
    assert linked.status_code == 200
    assert linked.json() == [
        {
            "workflow_id": workflow["id"],
            "name": "문의 처리",
            "description": None,
            "published_version_no": 1,
            "usable": True,
            "broken_reason": None,
        }
    ]
    assert (await client.post(f"{BASE}/{workflow['id']}/archive")).status_code == 200
    (row,) = (await client.get(path)).json()
    assert (row["usable"], row["broken_reason"]) == (False, "workflow_archived")


@pytest.mark.asyncio
async def test_tools_are_built_for_the_root_chat_run_only(
    client: AsyncClient, db: AsyncSession
) -> None:
    _, _, agent = await seed_agent(db)
    await db.commit()
    workflow = await _published(client, db, str(agent.id))
    await client.put(
        f"/api/agents/{agent.id}/workflow-tools", json={"workflow_ids": [workflow["id"]]}
    )

    chat = chat_tool.workflow_tools_for_run(
        await _cfg_with_tools(db, agent.id, uuid.uuid4()), str(uuid.uuid4())
    )
    (tool,) = chat
    assert tool.name == chat_tool.tool_name(workflow["id"])
    schema = tool.tool_call_schema.model_json_schema()  # type: ignore[union-attr]
    assert (
        list(schema["properties"]) == ["inquiry"]
        and schema["properties"]["inquiry"]["maxLength"] == 4000
    )
    for path in ("workflow", "subagent", "trigger", "api"):
        cfg = await _cfg_with_tools(db, agent.id, uuid.uuid4(), path)
        assert chat_tool.workflow_tools_for_run(cfg, None) == []


# --- calling the tool: wait, resume, idempotency ------------------------------------------


class State(TypedDict):
    result: str


def _graph(tool: Any) -> Any:
    async def node(_: State) -> State:
        call = {
            "args": {"inquiry": "환불해 주세요"},
            "id": "call-1",
            "name": tool.name,
            "type": "tool_call",
        }
        message = await tool.ainvoke(call)
        return {"result": message.content}

    builder = StateGraph(State)
    builder.add_node("call", node)  # type: ignore[arg-type]
    builder.add_edge(START, "call")
    builder.add_edge("call", END)
    return builder.compile(checkpointer=InMemorySaver())


@pytest.mark.asyncio
async def test_a_call_starts_one_run_waits_and_resumes_with_its_result(
    client: AsyncClient, db: AsyncSession
) -> None:
    _, _, agent = await seed_agent(db)
    await db.commit()
    workflow = await _published(client, db, str(agent.id))
    await client.put(
        f"/api/agents/{agent.id}/workflow-tools", json={"workflow_ids": [workflow["id"]]}
    )
    conversation_id = uuid.uuid4()
    chat_run_id = uuid.uuid4()
    (tool,) = chat_tool.workflow_tools_for_run(
        await _cfg_with_tools(db, agent.id, conversation_id), str(chat_run_id)
    )
    graph = _graph(tool)
    thread = {"configurable": {"thread_id": "t-1"}}

    first = await graph.ainvoke({"result": ""}, thread)
    assert "__interrupt__" in first, first
    (pending,) = first["__interrupt__"]
    assert pending.value["type"] == chat_tool.WAIT_TYPE
    (run,) = (await db.execute(sa.select(WorkflowRun))).scalars()
    assert pending.value["workflow_run_id"] == str(run.id)
    assert (
        run.parent_conversation_id,
        run.parent_tool_call_id,
        run.parent_conversation_run_id,
    ) == (
        conversation_id,
        "call-1",
        chat_run_id,
    )
    (event,) = (
        await db.execute(sa.select(AuditEvent).where(AuditEvent.action == "workflow.run_start"))
    ).scalars()
    metadata = event.event_metadata or {}
    assert metadata["started_from"] == "chat_tool"
    assert metadata["parent_conversation_id"] == str(conversation_id)

    # Resumed by hand before the run ended: the tool reports it as it is, nothing new starts.
    early = await graph.ainvoke(Command(resume={"status": "completed"}), thread)
    assert json.loads(early["result"])["status"] == "queued"

    # The run ends; a resumed call (same tool call id) reads its real result.
    run.status, run.output = "completed", {"answer": "환불 처리했습니다"}
    run.finished_at = datetime.now(UTC).replace(tzinfo=None)
    await db.commit()
    again = _graph(tool)
    done = await again.ainvoke({"result": ""}, {"configurable": {"thread_id": "t-2"}})
    assert json.loads(done["result"]) == {
        "status": "completed",
        "output": {"answer": "환불 처리했습니다"},
    }
    assert len((await db.execute(sa.select(WorkflowRun))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_a_run_that_cannot_start_answers_at_once(
    client: AsyncClient, db: AsyncSession
) -> None:
    _, _, agent = await seed_agent(db)
    await db.commit()
    workflow = await _published(client, db, str(agent.id))
    await client.put(
        f"/api/agents/{agent.id}/workflow-tools", json={"workflow_ids": [workflow["id"]]}
    )
    (tool,) = chat_tool.workflow_tools_for_run(
        await _cfg_with_tools(db, agent.id, uuid.uuid4()), None
    )
    # The linked agent changed after publishing: the run is refused, the chat does not wait.
    agent.system_prompt = "바뀐 지시"
    await db.commit()
    result = await _graph(tool).ainvoke({"result": ""}, {"configurable": {"thread_id": "t-3"}})
    assert "__interrupt__" not in result
    assert json.loads(result["result"])["code"] == "WORKFLOW_AGENT_CHANGED"


# --- the resume sweep --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_sweep_picks_ended_chat_runs_once_and_skips_chats_no_longer_waiting(
    client: AsyncClient, db: AsyncSession
) -> None:
    _, _, agent = await seed_agent(db)
    await db.commit()
    workflow = await _published(client, db, str(agent.id))
    from app.models.workflow import WorkflowVersion

    version_id = (await db.execute(sa.select(WorkflowVersion.id))).scalar_one()
    now = datetime.now(UTC).replace(tzinfo=None)

    def run(status: str, *, parent: bool) -> WorkflowRun:
        return WorkflowRun(
            workflow_id=uuid.UUID(workflow["id"]),
            version_id=version_id,
            user_id=TEST_USER_ID,
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            status=status,
            input={},
            parent_conversation_id=uuid.uuid4() if parent else None,
            parent_tool_call_id=f"call-{uuid.uuid4().hex[:6]}" if parent else None,
            finished_at=now,
        )

    ended, running, plain = (
        run("completed", parent=True),
        run("running", parent=True),
        run("failed", parent=False),
    )
    db.add_all([ended, running, plain])
    await db.commit()

    due = await chat_resume._due()
    assert [row[0] for row in due] == [ended.id]
    # Its conversation does not exist any more: nothing to resume, marked once.
    assert await chat_resume.resume_finished_chats() == {"not_waiting": 1}
    assert await chat_resume.resume_finished_chats() == {}
    await db.refresh(ended)
    assert ended.chat_resumed_at is not None


def test_the_waiting_interrupt_is_found_by_its_run() -> None:
    run_id = uuid.uuid4()
    tasks = [
        {
            "interrupts": [
                {"id": "a", "value": {"action_requests": []}, "ns": []},
                {
                    "id": "b",
                    "value": {"type": "workflow_wait", "workflow_run_id": str(uuid.uuid4())},
                    "ns": [],
                },
                {
                    "id": "c",
                    "value": {"type": "workflow_wait", "workflow_run_id": str(run_id)},
                    "ns": ["x"],
                },
            ]
        }
    ]
    found = chat_resume._waiting_interrupt(tasks, run_id)
    assert found is not None and found["id"] == "c"
    assert chat_resume._waiting_interrupt(tasks, uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_linking_a_workflow_tool_does_not_change_the_agent_fingerprint(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A step never gets workflow tools, so workflows using the agent need no republish."""
    from app.workflows.pins import agent_fingerprints

    _, _, agent = await seed_agent(db)
    await db.commit()
    workflow = await _published(client, db, str(agent.id))
    before = await agent_fingerprints(db, [agent.id])
    await client.put(
        f"/api/agents/{agent.id}/workflow-tools", json={"workflow_ids": [workflow["id"]]}
    )
    assert await agent_fingerprints(db, [agent.id]) == before


@pytest.mark.asyncio
async def test_the_chat_card_finds_the_run_of_its_tool_call_and_no_one_elses(
    client: AsyncClient, db: AsyncSession
) -> None:
    _, _, agent = await seed_agent(db)
    await db.commit()
    workflow = await _published(client, db, str(agent.id))
    from app.models.workflow import WorkflowVersion

    version_id = (await db.execute(sa.select(WorkflowVersion.id))).scalar_one()
    conversation_id = uuid.uuid4()
    mine = WorkflowRun(
        workflow_id=uuid.UUID(workflow["id"]),
        version_id=version_id,
        user_id=TEST_USER_ID,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        status="waiting",
        input={},
        parent_conversation_id=conversation_id,
        parent_tool_call_id="call-9",
    )
    theirs = WorkflowRun(
        workflow_id=uuid.UUID(workflow["id"]),
        version_id=version_id,
        user_id=uuid.uuid4(),
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        status="waiting",
        input={},
        parent_conversation_id=conversation_id,
        parent_tool_call_id="call-10",
    )
    db.add_all([mine, theirs])
    await db.commit()
    path = "/api/workflow-runs/by-tool-call"
    found = await client.get(
        path, params={"conversation_id": str(conversation_id), "tool_call_id": "call-9"}
    )
    assert found.status_code == 200
    body = found.json()
    assert (body["id"], body["status"], body["parent_conversation_id"]) == (
        str(mine.id),
        "waiting",
        str(conversation_id),
    )
    other = await client.get(
        path, params={"conversation_id": str(conversation_id), "tool_call_id": "call-10"}
    )
    assert other.status_code == 404
