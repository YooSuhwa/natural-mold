"""Runtime eager child queries must not write before capability denial."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, assert_never
from unittest.mock import Mock

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.skill import Skill
from app.services import agent_invocation_service
from app.services.chat import runtime_context
from app.services.chat.secrets import collect_conversation_secret_values

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_secret_collector import CollectorCase

pytest_plugins = ["tests.authz.test_private_secret_collector"]
SOURCE_ROOT: Final = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("surface", ["collector", "config"])
async def test_runtime_eager_prefix_denies_before_pending_private_sql(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    collector_case: CollectorCase,
    surface: Literal["collector", "config"],
) -> None:
    assert Path(runtime_context.__file__).is_relative_to(SOURCE_ROOT)
    own, foreign = private_scopes.first, private_scopes.second
    row = Skill(
        user_id=foreign.user_id,
        org_id=foreign.org_id,
        tenant_id=foreign.tenant_id,
        name="Foreign original",
        slug="f4-eager-foreign",
        kind="text",
    )
    db.add(row)
    await db.commit()
    target_id = row.id
    row.name = "EAGER UNAUTHORIZED PRE-DENIAL WRITE"
    flush = Mock()
    event.listen(db.sync_session, "before_flush", flush)
    try:
        match surface:
            case "collector":
                await collect_conversation_secret_values(db, collector_case.conversation, own)
            case "config":
                agent_id = collector_case.conversation.agent_id
                assert agent_id is not None
                agent = await runtime_context.get_agent_with_tools(db, agent_id, own)
                assert agent is not None
                user = CurrentUser(own.user_id, "dummy@example.test", "Fixture")
                await agent_invocation_service.build_agent_config_for_loaded_agent(
                    db,
                    agent,
                    context=own,
                    thread_id=str(collector_case.conversation.id),
                    principal=agent_invocation_service.AgentInvocationPrincipal.chat_user(user),
                    source="chat",
                )
            case unreachable:
                assert_never(unreachable)
    except AppError as denied:
        assert denied.status == 404
    else:
        pytest.fail("Expected private child denial")
    finally:
        event.remove(db.sync_session, "before_flush", flush)
    with db.no_autoflush:
        stored = await db.scalar(select(Skill.name).where(Skill.id == target_id))
    remains_staged = row in db.dirty
    await db.rollback()
    print(
        f"F4 runtime surface={surface} denied=404 flushes={flush.call_count} "
        f"stored={stored!r} remains_staged={remains_staged}"
    )
    assert flush.call_count == 0
    assert stored == "Foreign original"
    assert remains_staged
