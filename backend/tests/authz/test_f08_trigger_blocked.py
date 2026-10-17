"""F-08 (plan 11.1, 11.6): a trigger refused at dispatch records a BLOCKED run.

``status=failed`` + ``reason_code`` (the auto pause still counts it), ``run.blocked``
audit, never an invoke; OpenFGA down is ``skipped``; legacy keeps the silent skip.
"""

from collections.abc import Iterable
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import runtime
from app.authz.service import AuthzService
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.audit_event import AuditEvent
from app.models.organization import OrganizationMember
from app.models.tool import AgentToolLink, Tool
from tests.authz.authz_service_testkit import RecordingClient
from tests.authz.e01_support import THIRD_USER_ID, add_agent, add_member, seed
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession

ME = f"user:{TEST_USER_ID}"


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


def _install(monkeypatch, mode: str, allowed: Iterable[tuple[str, str, str]] = (), **kw) -> None:
    client = RecordingClient(allowed=frozenset(allowed), **kw)
    monkeypatch.setattr(runtime, "_current", AuthzService(client, mode=mode))  # type: ignore[arg-type]


def _runnable(agent: Agent) -> set[tuple[str, str, str]]:
    return {(ME, "can_run", f"agent:{agent.id}"), (ME, "can_use", f"llm_model:{agent.model_id}")}


async def _trigger(db: AsyncSession, agent: Agent, *, pause_after: int | None = 3) -> AgentTrigger:
    trigger = AgentTrigger(
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        agent_id=agent.id,
        user_id=TEST_USER_ID,
        trigger_type="interval",
        schedule_config={"interval_minutes": 10},
        input_message="go",
        status="active",
        run_count=0,
        auto_pause_after_failures=pause_after,
    )
    db.add(trigger)
    await db.commit()
    return trigger


async def _fire(trigger: AgentTrigger) -> AsyncMock:
    invoke = AsyncMock(return_value="never")
    with (
        patch("app.agent_runtime.trigger_executor.execute_agent_invoke", invoke),
        patch("app.agent_runtime.trigger_executor.async_session", TestSession),
    ):
        from app.agent_runtime.trigger_executor import execute_trigger

        await execute_trigger(str(trigger.id))
    return invoke


async def _state(trigger_id: UUID) -> tuple[list[AgentTriggerRun], AgentTrigger, list[Any]]:
    async with TestSession() as check:
        runs = list(
            await check.scalars(
                select(AgentTriggerRun).where(AgentTriggerRun.trigger_id == trigger_id)
            )
        )
        trigger = await check.get(AgentTrigger, trigger_id)
        audits = list(
            await check.scalars(select(AuditEvent).where(AuditEvent.action == "run.blocked"))
        )
    assert trigger is not None
    return runs, trigger, audits


async def test_revoked_owner_records_permission_revoked_and_pauses(db, monkeypatch) -> None:
    agent = await add_agent(db)
    trigger = await _trigger(db, agent, pause_after=1)
    _install(monkeypatch, "enforce")
    invoke = await _fire(trigger)
    invoke.assert_not_called()
    [run], fresh, [audit] = await _state(trigger.id)
    assert (run.status, run.reason_code) == ("failed", "permission_revoked")
    assert run.error_message == "permission_revoked: AGENT_NOT_FOUND"
    assert (fresh.failure_count, fresh.status, fresh.last_status) == (1, "paused", "failed")
    assert (audit.reason_code, audit.outcome, audit.target_id) == (
        "permission_revoked",
        "denied",
        str(agent.id),
    )
    assert audit.run_id == str(run.id) and audit.event_metadata["invoker_type"] == "schedule"
    assert (audit.owner_user_id, audit.target_owner_user_id) == (TEST_USER_ID, agent.user_id)


async def test_a_streak_of_the_same_reason_is_audited_once(db, monkeypatch) -> None:
    agent = await add_agent(db)
    trigger = await _trigger(db, agent, pause_after=None)
    _install(monkeypatch, "enforce")
    await _fire(trigger)
    await _fire(trigger)
    runs, fresh, audits = await _state(trigger.id)
    assert [run.reason_code for run in runs] == ["permission_revoked"] * 2
    assert fresh.failure_count == 2 and len(audits) == 1  # recorded twice, announced once


async def test_owner_who_left_the_org_is_owner_left(db, monkeypatch) -> None:
    agent = await add_agent(db)
    trigger = await _trigger(db, agent)
    await db.execute(
        update(OrganizationMember)
        .where(OrganizationMember.user_id == TEST_USER_ID, OrganizationMember.org_id == TEST_ORG_ID)
        .values(status="removed")
    )
    await db.commit()
    _install(monkeypatch, "enforce", _runnable(agent))
    (await _fire(trigger)).assert_not_called()
    [run], fresh, _ = await _state(trigger.id)
    assert (run.status, run.reason_code, fresh.failure_count) == ("failed", "owner_left", 1)


async def test_authz_unavailable_is_skipped_and_never_counts(db, monkeypatch) -> None:
    agent = await add_agent(db)
    trigger = await _trigger(db, agent, pause_after=1)
    _install(monkeypatch, "enforce", unavailable=True)
    (await _fire(trigger)).assert_not_called()
    [run], fresh, audits = await _state(trigger.id)
    assert (run.status, run.reason_code) == ("skipped", "authz_unavailable")
    assert (fresh.failure_count, fresh.status) == (0, "active")
    assert audits == []


async def test_denied_required_subagent_is_required_unavailable(db, monkeypatch) -> None:
    await add_member(db, THIRD_USER_ID)
    agent = await add_agent(db)
    child = await add_agent(db, owner=THIRD_USER_ID)  # agent:A has no can_run on it (11.1)
    db.add(AgentSubAgentLink(parent_agent_id=agent.id, sub_agent_id=child.id, required=True))
    trigger = await _trigger(db, agent)
    _install(monkeypatch, "enforce", _runnable(agent))
    (await _fire(trigger)).assert_not_called()
    [run], _, [audit] = await _state(trigger.id)
    assert (run.status, run.reason_code) == ("failed", "required_unavailable")
    assert run.error_message == "required_unavailable: agent_not_ready"
    assert audit.event_metadata["code"] == "agent_not_ready"


async def test_required_broken_link_is_delegation_broken(db, monkeypatch) -> None:
    agent = await add_agent(db)
    tool = Tool(is_system=True, name="Web", definition_key="builtin:web_search", description="w")
    db.add(tool)
    await db.flush()
    db.add(
        AgentToolLink(
            agent_id=agent.id,
            tool_id=tool.id,
            required=True,
            link_status="broken",
            broken_reason="delegator_lost_access",
        )
    )
    trigger = await _trigger(db, agent)
    _install(monkeypatch, "enforce", _runnable(agent))
    (await _fire(trigger)).assert_not_called()
    [run], _, _ = await _state(trigger.id)
    assert (run.status, run.reason_code) == ("failed", "delegation_broken")


async def test_legacy_keeps_the_silent_skip(db, monkeypatch) -> None:
    agent = await add_agent(db)
    trigger = await _trigger(db, agent)
    await db.execute(
        update(OrganizationMember)
        .where(OrganizationMember.user_id == TEST_USER_ID)
        .values(status="removed")
    )
    await db.commit()
    _install(monkeypatch, "legacy")
    (await _fire(trigger)).assert_not_called()
    runs, fresh, audits = await _state(trigger.id)
    assert (runs, audits, fresh.failure_count) == ([], [], 0)


async def test_permitted_child_of_another_member_runs(db, monkeypatch) -> None:
    await add_member(db, THIRD_USER_ID)
    agent = await add_agent(db)
    child = await add_agent(db, owner=THIRD_USER_ID)
    db.add(AgentSubAgentLink(parent_agent_id=agent.id, sub_agent_id=child.id, required=True))
    trigger = await _trigger(db, agent)
    allowed = _runnable(agent) | {
        (f"agent:{agent.id}", "can_run", f"agent:{child.id}"),
        (f"user:{THIRD_USER_ID}", "can_use", f"llm_model:{child.model_id}"),  # child's account
    }
    _install(monkeypatch, "enforce", allowed)
    # Patched: this test pins the plan; the unpatched child build has its own test below.
    children = AsyncMock()
    monkeypatch.setattr("app.agent_runtime.trigger_executor._configure_trigger_subagents", children)
    (await _fire(trigger)).assert_called_once()
    assert children.await_args is not None
    [build] = children.await_args.args
    assert [link.sub_agent_id for link in build.config.access_plan.sub_agent_links] == [child.id]
    [run], _, audits = await _state(trigger.id)
    assert (run.status, run.reason_code, audits) == ("success", None, [])


async def test_legacy_trigger_with_a_subagent_builds_the_child(db, monkeypatch) -> None:
    """Unpatched child build: the tree lock's populate_existing must not leave the
    child's relationships unloaded (the loop reads child.model, links, skills)."""
    agent = await add_agent(db)
    child = await add_agent(db)
    db.add(AgentSubAgentLink(parent_agent_id=agent.id, sub_agent_id=child.id))
    trigger = await _trigger(db, agent)
    _install(monkeypatch, "legacy")
    key = AsyncMock(return_value="sk-test")  # the child's LLM key (no credential seeded)
    monkeypatch.setattr("app.agent_runtime.subagents.resolve_llm_api_key_for_agent", key)
    invoke = await _fire(trigger)
    invoke.assert_called_once()
    [config] = [call.args[0] for call in invoke.await_args_list]
    assert len(config.subagents_config) == 1
    [run], _, _ = await _state(trigger.id)
    assert run.status == "success"
