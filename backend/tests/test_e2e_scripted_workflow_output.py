"""The scripted E2E model answers a workflow step's output schema (순번 23 W3b)."""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage

from app.agent_runtime.e2e_scripted_model import E2EScriptedChatModel
from app.agent_runtime.runtime_agent_builder import build_agent
from app.agent_runtime.structured_output import response_format_for, structured_outcome

SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": ["refund", "bug"]},
        "urgent": {"type": "boolean"},
        "note": {"type": "string"},
        "count": {"type": "integer"},
    },
    "required": ["category", "urgent"],
}


@pytest.mark.asyncio
@pytest.mark.parametrize(("text", "urgent"), [("문의", False), ("E2E_WORKFLOW_YES 문의", True)])
async def test_the_scripted_model_answers_in_the_schema(text: str, urgent: bool) -> None:
    agent = build_agent(
        E2EScriptedChatModel(), [], "x", response_format=response_format_for(SCHEMA)
    )
    result = await agent.ainvoke({"messages": [HumanMessage(text)]})
    outcome = structured_outcome(result, SCHEMA)
    assert outcome.status == "ok"
    assert outcome.value == {"category": "refund", "urgent": urgent, "note": "E2E 응답", "count": 1}


@pytest.mark.asyncio
async def test_the_scripted_model_answers_workflow_editing_through_the_server_checks() -> None:
    """W5: on an empty draft it builds start → agent → output that passes the checks; the ask
    marker makes it ask back."""
    from app.workflows import ai_edit
    from app.workflows.checks import graph_issues
    from app.workflows.definition import WorkflowDefinition, empty_definition

    bound = E2EScriptedChatModel().bind_tools([ai_edit.tool_spec()], tool_choice="WorkflowEdit")

    async def model(messages: list) -> ai_edit.ModelTurn:
        reply = await bound.ainvoke(messages)
        (call,) = reply.tool_calls
        return ai_edit.ModelTurn(call["args"], reply, call["id"])

    agents = [ai_edit.OfferedAgent("11111111-1111-4111-8111-111111111111", "조사", "")]
    empty = WorkflowDefinition.model_validate(empty_definition())
    proposal = await ai_edit.propose(empty, agents, "조사 흐름", [], model=model)
    assert (proposal.kind, proposal.attempts, proposal.issues) == ("proposal", 1, [])
    assert proposal.definition is not None
    built = WorkflowDefinition.model_validate(proposal.definition)
    assert [node.id for node in built.nodes] == ["start", "research", "done"]
    assert graph_issues(built) == []

    asked = await ai_edit.propose(empty, agents, "E2E_WORKFLOW_ASK 흐름", [], model=model)
    assert asked.kind == "question"


@pytest.mark.asyncio
async def test_the_scripted_chat_model_calls_a_workflow_tool_then_answers_with_its_result() -> None:
    """workflow-tool-plan: the marker makes it call the first workflow tool, then report back."""
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel

    class Args(BaseModel):
        inquiry: str

    tool = StructuredTool.from_function(
        coroutine=lambda inquiry: inquiry, name="workflow_abc", description="x", args_schema=Args
    )
    bound = E2EScriptedChatModel().bind_tools([tool])
    ask = HumanMessage("E2E_CALL_WORKFLOW 환불해 주세요")
    first = await bound.ainvoke([ask])
    (call,) = first.tool_calls
    assert (call["name"], call["args"]) == ("workflow_abc", {"inquiry": ask.content})
    reply = await bound.ainvoke(
        [
            ask,
            AIMessage(content="", tool_calls=[call]),
            ToolMessage('{"status": "completed"}', tool_call_id=call["id"], name="workflow_abc"),
        ]
    )
    assert "워크플로 결과를 받았어요" in reply.content
    plain = await bound.ainvoke([HumanMessage("안녕")])
    assert not plain.tool_calls
