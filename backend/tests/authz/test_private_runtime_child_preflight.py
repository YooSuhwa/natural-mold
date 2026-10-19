"""R16 bounded context witnesses; production modules must be the frozen review checkout."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, assert_never
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import mcp_app_scope, trigger_scope
from app.exceptions import AppError
from app.mcp import apps_binding_store
from app.mcp.apps_binding_store import McpAppsError
from app.models.agent import Agent
from app.models.agent_trigger import AgentTrigger
from app.models.conversation import Conversation
from app.services.chat.conversations import create_conversation

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase

pytest_plugins = ["tests.authz.test_private_agent_api_scope"]
SOURCE_ROOT: Final = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("surface", ["mcp_agent", "mcp_run", "mcp_binding", "trigger"])
async def test_complete_parent_denial_preserves_staged_content(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    api_case: ApiCase,
    pending: bool,
    surface: Literal["mcp_agent", "mcp_run", "mcp_binding", "trigger"],
) -> None:
    source_root = SOURCE_ROOT
    for module in (mcp_app_scope, trigger_scope, apps_binding_store):
        assert module.__file__ is not None
        assert Path(module.__file__).is_relative_to(source_root)
    own = private_scopes.first
    conversation = await create_conversation(db, api_case.first.agent_id, context=own)
    trigger = AgentTrigger(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        agent_id=api_case.second.agent_id,
        trigger_type="interval",
        schedule_config={"interval_minutes": 15},
        input_message="Context witness",
    )
    db.add(trigger)
    await db.commit()
    if surface == "mcp_agent":
        await db.execute(
            update(Conversation)
            .where(Conversation.id == conversation.id)
            .values(agent_id=api_case.second.agent_id)
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    agent = await db.get(Agent, api_case.first.agent_id)
    assert agent is not None
    original = agent.name
    if pending:
        agent.name = "Staged R16 context content"
    flush = Mock()
    event.listen(db.sync_session, "before_flush", flush)
    denied = False
    try:
        match surface:
            case "mcp_agent" | "mcp_run":
                denied = (
                    await mcp_app_scope.load_private_mcp_app_run(
                        db, mcp_app_scope.McpAppRunTarget(conversation.id, uuid4()), own
                    )
                    is None
                )
            case "mcp_binding":
                await apps_binding_store.authorize_binding(
                    db,
                    conversation_id=conversation.id,
                    run_id=uuid4(),
                    tool_call_id="missing-owned-tool-call",
                    context=own,
                )
            case "trigger":
                await trigger_scope.require_trigger_actor(db, trigger)
            case unreachable:
                assert_never(unreachable)
    except (AppError, McpAppsError):
        denied = True
    finally:
        event.remove(db.sync_session, "before_flush", flush)
    with db.no_autoflush:
        stored = await db.scalar(select(Agent.name).where(Agent.id == agent.id))
    staged = agent in db.dirty
    await db.rollback()
    assert denied
    assert (flush.call_count, stored, staged) == (0, original, pending)
