"""Static inventory: execution consumers obtain the proof before envelope/policy/run work."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.agent import Agent
from app.models.organization import Organization
from app.schemas.agent import AgentCreate
from app.services import agent_service
from tests.authz.execution_gateway_testkit import seed_graph

APP = Path(__file__).resolve().parents[2] / "app"

# Callers of the compatibility gateways. Each one acquires (or reuses) the
# canonical proof inside the gateway itself; a new caller must be reviewed.
POLICY_CALLERS = {
    "agent_api/run_acceptance.py",
    "agent_api/thread_runtime.py",
    "services/chat/chat_execution_preparation.py",
    "services/conversation_run_queue_persistence.py",
    "services/conversation_side_chat_service.py",
    "services/trigger_conversation_runtime.py",
}
CREATE_RUN_CALLERS = {
    "routers/e2e_chat_run_helpers.py",
    "services/e2e_mcp_apps_fixture_service.py",
}


def _calls(*names: str) -> set[str]:
    found: set[str] = set()
    for path in APP.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if called in names:
                    found.add(path.relative_to(APP).as_posix())
    return found


def _first_call_line(path: str, function: str, name: str) -> int:
    tree = ast.parse((APP / path).read_text())
    target = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == function
    )
    lines = [
        node.lineno
        for node in ast.walk(target)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "id", None) or getattr(node.func, "attr", None)) == name
    ]
    return min(lines)


def test_rejects_late_or_unlisted_create_run_consumer() -> None:
    policy = _calls("ensure_conversation_runtime_policy", "_ensure_conversation_runtime_policy") - {
        "services/conversation_runtime_policy.py"
    }
    create = _calls("create_run") - {"services/conversation_run_creation.py"}
    assert policy == POLICY_CALLERS
    assert create - {"routers/skill_evaluations.py"} == CREATE_RUN_CALLERS


def test_acceptance_and_creation_ingresses_take_proof_first() -> None:
    acceptance = "services/agent_execution_acceptance.py"
    assert _first_call_line(acceptance, "accept_owned_execution", "acquire_execution_proof") < (
        _first_call_line(acceptance, "accept_owned_execution", "_validated_envelope")
    )
    for path, function in (
        ("services/agent_creation_service.py", "create_agent"),
        ("services/builder_agent_finalization.py", "confirm_build"),
        ("services/skill_builder_hidden_agent.py", "get_or_create_skill_builder_agent"),
    ):
        assert _first_call_line(path, function, "require_execution_prefix") < _first_call_line(
            path, function, "Agent"
        )


@pytest.mark.asyncio
async def test_archived_organization_denies_agent_creation(db: AsyncSession) -> None:
    graph = await seed_graph(db, conversations=0)
    organization = await db.get(Organization, graph.context.org_id)
    assert organization is not None
    agent = await db.get(Agent, graph.agent_id)
    assert agent is not None
    organization.status, organization.archived_at = "archived", datetime.now(UTC)
    await db.commit()

    with pytest.raises(AppError):
        await agent_service.create_agent(
            db, AgentCreate(name="x", system_prompt="y", model_id=agent.model_id), graph.context
        )
