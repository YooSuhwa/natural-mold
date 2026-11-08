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
