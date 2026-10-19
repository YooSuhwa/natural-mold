"""Captured private readers refresh authority before metadata or SQL effects."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.organization import OrganizationMember
from app.models.user import User
from app.services.chat.conversations import create_conversation

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase
pytest_plugins = ["tests.authz.test_private_agent_api_scope"]
Action = Literal[
    "artifact_list",
    "artifact_message",
    "artifact_library",
    "artifact_recent",
    "artifact_stats",
    "mcp_all",
    "mcp_server",
]
Change = Literal["active", "membership", "account"]
RunAction = Literal[
    "active_run",
    "active_runs",
    "current_run",
    "latest_run",
    "specific_run",
    "interrupted_run",
    "tool_list",
]


@pytest.mark.parametrize("change", ["active", "membership", "account", "foreign_context"])
@pytest.mark.parametrize(
    "action",
    [
        "active_run",
        "active_runs",
        "current_run",
        "latest_run",
        "specific_run",
        "interrupted_run",
        "tool_list",
    ],
)
async def test_run_and_tool_reads_denies_revoked_actor(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    api_case: ApiCase,
    action: RunAction,
    change: str,
) -> None:
    from app.models.conversation_run import ConversationRun
    from app.models.tool import Tool
    from app.services import conversation_run_service as runs
    from app.services import tool_service

    own = private_scopes.first
    conversation = await create_conversation(db, api_case.first.agent_id, context=own)
    run = ConversationRun(
        conversation_id=conversation.id,
        agent_id=api_case.first.agent_id,
        user_id=own.user_id,
        source="chat",
        status="interrupted" if action == "interrupted_run" else "running",
        is_active=action != "interrupted_run",
        input_preview="PRIVATE_F4_RUN_INPUT",
        error_message="PRIVATE_F4_ERROR",
    )
    tool = Tool(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="PRIVATE_F4_BUILTIN",
        definition_key="fixture",
        parameters={"private": "PRIVATE_F4_PARAMETERS"},
    )
    db.add_all([run, tool])
    await db.commit()
    match change:
        case "active" | "foreign_context":
            pass
        case "membership":
            await db.execute(
                update(OrganizationMember)
                .where(
                    OrganizationMember.org_id == own.org_id,
                    OrganizationMember.user_id == own.user_id,
                )
                .values(status="removed")
                .execution_options(synchronize_session=False)
            )
            await db.commit()
        case "account":
            await db.execute(
                update(User)
                .where(User.id == own.user_id)
                .values(is_active=False)
                .execution_options(synchronize_session=False)
            )
            await db.commit()
        case _:
            pytest.fail("unexpected change")
    context = private_scopes.second if change == "foreign_context" else own
    disclosed = False
    try:
        match action:
            case "active_run":
                loaded = await runs.get_active_run(
                    db, conversation_id=conversation.id, context=context
                )
                disclosed = loaded is not None and loaded.input_preview == run.input_preview
            case "active_runs":
                mapped = await runs.active_runs_for_conversations(db, [conversation.id], context)
                disclosed = conversation.id in mapped
            case "current_run":
                loaded = await runs.current_run_for_conversation(
                    db, conversation_id=conversation.id, context=context
                )
                disclosed = loaded is not None and loaded.input_preview == run.input_preview
            case "latest_run":
                loaded = await runs.latest_run_for_conversation(
                    db, conversation_id=conversation.id, context=context
                )
                disclosed = loaded is not None and loaded.input_preview == run.input_preview
            case "specific_run":
                loaded = await runs.get_run_for_user(
                    db, conversation_id=conversation.id, run_id=run.id, context=context
                )
                disclosed = loaded is not None and loaded.input_preview == run.input_preview
            case "interrupted_run":
                loaded = await runs.get_latest_interrupted_run(
                    db, conversation_id=conversation.id, context=context
                )
                disclosed = loaded is not None and loaded.input_preview == run.input_preview
            case "tool_list":
                tool_rows = await tool_service.list_tools(db, context)
                disclosed = any(row.parameters == tool.parameters for row in tool_rows)
            case unreachable:
                assert_never(unreachable)
    except AppError as error:
        assert error.status in {403, 404}
    assert disclosed == (change == "active")
