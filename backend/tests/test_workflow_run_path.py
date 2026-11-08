"""Workflow W0: the ``workflow`` run path across identity, authz, memory and attribution."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.agent_runtime import memory_paths
from app.agent_runtime.identity import (
    AGENT_IDENTITY_FIXED,
    AGENT_IDENTITY_PER_USER,
    AgentIdentityError,
    AgentRunSource,
    resolve_agent_run_identity,
)
from app.agent_runtime.memory_paths import MemoryPathAccess, memory_access
from app.agent_runtime.subagents import _child_source
from app.approvals import service as approvals
from app.authz.tuple_types import RUN_SOURCES
from app.credentials.run_subject import subject_for_agent
from app.models.agent import Agent
from app.services.agent_invocation_service import _source_to_agent_run_source
from app.tenancy.attribution import attribute_child, attribute_run
from tests.authz.unit_scope_fixture import unit_context

OWNER, RUNNER = uuid.uuid4(), uuid.uuid4()


def _identity(mode: str, source: AgentRunSource, caller: uuid.UUID | None = RUNNER) -> Any:
    return resolve_agent_run_identity(
        agent_id=uuid.uuid4(),
        agent_owner_user_id=OWNER,
        runtime_name="agent_abcdef12",
        identity_mode=mode,
        source=source,
        caller_user_id=caller,
    )


def _agent(mode: str) -> Agent:
    return cast(Agent, SimpleNamespace(id=uuid.uuid4(), user_id=OWNER, identity_mode=mode))


def test_a_workflow_step_reads_agent_memory_and_writes_none() -> None:
    assert memory_access("workflow") == MemoryPathAccess(
        user_scope=False, agent_scope=True, write_tools=False, memo_mount=False
    )
    assert memory_paths.allowed_scopes_for("workflow", "both") == "agent"
    assert memory_paths.allowed_scopes_for("workflow", "user") is None
    assert memory_paths._RUN_SOURCES["WORKFLOW"] == "workflow"


@pytest.mark.asyncio
async def test_a_resumed_workflow_step_keeps_its_path(monkeypatch: pytest.MonkeyPatch) -> None:
    first = SimpleNamespace(run_source="WORKFLOW")

    async def origin(*_: Any) -> Any:
        return first

    monkeypatch.setattr(memory_paths, "origin_run", origin)
    assert await memory_paths.origin_run_path(cast(Any, None), cast(Any, first)) == "workflow"


def test_per_user_agents_act_as_the_person_who_started_the_workflow() -> None:
    assert _identity(
        AGENT_IDENTITY_PER_USER, AgentRunSource.WORKFLOW
    ).credential_subject_user_id == (RUNNER)
    assert _identity(AGENT_IDENTITY_FIXED, AgentRunSource.WORKFLOW).credential_subject_user_id == (
        OWNER
    )
    with pytest.raises(AgentIdentityError):
        _identity(AGENT_IDENTITY_PER_USER, AgentRunSource.WORKFLOW, caller=None)
    # A schedule still never borrows a person's connections.
    with pytest.raises(AgentIdentityError):
        _identity(AGENT_IDENTITY_PER_USER, AgentRunSource.TRIGGER)


@pytest.mark.parametrize("mode", [AGENT_IDENTITY_PER_USER, AGENT_IDENTITY_FIXED])
def test_the_authorization_subject_carries_the_workflow_source(mode: str) -> None:
    subject = subject_for_agent(
        _agent(mode), unit_context(RUNNER), _identity(mode, AgentRunSource.WORKFLOW)
    )
    assert subject.principal.run_source == "WORKFLOW"
    assert subject.principal.kind == ("user" if mode == AGENT_IDENTITY_PER_USER else "agent")


def test_mcp_delegations_cover_workflow_runs() -> None:
    assert "WORKFLOW" in RUN_SOURCES


def test_attribution_bills_the_runner_as_a_person() -> None:
    context = unit_context(RUNNER)
    run = attribute_run("workflow", context, _agent(AGENT_IDENTITY_PER_USER))
    assert (run.run_source, run.invoker_type) == ("WORKFLOW", "user")
    assert run.on_behalf_of_user_id == run.credential_subject_user_id == RUNNER
    fixed = attribute_run("workflow", context, _agent(AGENT_IDENTITY_FIXED))
    assert fixed.credential_subject_user_id == OWNER
    child = attribute_child(run, _agent(AGENT_IDENTITY_PER_USER))
    assert (child.run_source, child.credential_subject_user_id) == ("SUBAGENT", RUNNER)
    # Unchanged: a schedule is not a person.
    assert attribute_run("trigger", context, _agent(AGENT_IDENTITY_PER_USER)).invoker_type == (
        "schedule"
    )


def test_entry_and_child_sources() -> None:
    assert _source_to_agent_run_source("workflow") is AgentRunSource.WORKFLOW
    assert _source_to_agent_run_source("trigger") is AgentRunSource.TRIGGER
    cfg = SimpleNamespace(run_path="workflow")
    assert _child_source(cast(Any, cfg), is_trigger_mode=True) is AgentRunSource.WORKFLOW
    cfg.run_path = "trigger"
    assert _child_source(cast(Any, cfg), is_trigger_mode=True) is AgentRunSource.TRIGGER
    cfg.run_path = "chat"
    assert _child_source(cast(Any, cfg), is_trigger_mode=False) is AgentRunSource.CHAT


def test_approval_requests_from_a_workflow_run_say_so() -> None:
    assert approvals._SOURCES["WORKFLOW"] == "workflow"
