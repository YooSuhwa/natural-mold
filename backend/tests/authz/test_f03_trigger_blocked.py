"""F-03 (plan 10.6, agent_trigger_runs reason ``delegation_broken``): a trigger whose
agent has a required broken link records a failed run with a clear reason instead of
silently skipping, and never invokes the agent."""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.tool import AgentToolLink, Tool
from tests.authz.e01_support import add_agent, seed
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


async def test_required_broken_link_fails_the_trigger_run(db: AsyncSession) -> None:
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
    )
    db.add(trigger)
    await db.commit()
    invoke = AsyncMock(return_value="never")
    with (
        patch("app.agent_runtime.trigger_executor.execute_agent_invoke", invoke),
        patch("app.agent_runtime.trigger_executor.async_session", TestSession),
    ):
        from app.agent_runtime.trigger_executor import execute_trigger

        await execute_trigger(str(trigger.id))
    invoke.assert_not_called()
    async with TestSession() as check:
        run = await check.scalar(
            select(AgentTriggerRun).where(AgentTriggerRun.trigger_id == trigger.id)
        )
    assert run is not None and run.status == "failed"
    assert run.error_message is not None
    assert run.error_message.startswith("delegation_broken: required link unavailable")
    assert str(tool.id) in run.error_message
