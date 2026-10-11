"""Private runtime capability denial must precede a Trigger's durable claim."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import trigger_executor
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.tool import AgentToolLink, Tool

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.test_private_trigger_scope"]


async def test_foreign_private_tool_denial_never_claims_or_blocks_trigger_retry(
    db: AsyncSession, private_trigger: AgentTrigger, private_scopes: PrivateScopes
) -> None:
    foreign = private_scopes.second
    tool = Tool(
        user_id=foreign.user_id,
        org_id=foreign.org_id,
        tenant_id=foreign.tenant_id,
        name="Foreign trigger tool",
        definition_key="builtin:current_datetime",
        parameters={"private_parameter": "Other company"},
    )
    db.add(tool)
    await db.flush()
    db.add(AgentToolLink(agent_id=private_trigger.agent_id, tool_id=tool.id))
    await db.commit()
    for _ in range(2):
        run = await trigger_executor.execute_trigger(str(private_trigger.id), force=True)
        assert run is None
    assert await db.scalar(select(AgentTriggerRun.id)) is None
    await db.refresh(private_trigger)
    assert private_trigger.status == "active" and private_trigger.failure_count == 0
