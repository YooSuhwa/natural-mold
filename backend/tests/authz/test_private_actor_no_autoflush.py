"""Captured private readers refresh authority before metadata or SQL effects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, assert_never
from unittest.mock import Mock

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import (
    agent_scope,
    builder_scope,
    conversation_run_scope,
    conversation_scope,
    private_actor,
    skill_binding_scope,
    skill_builder_scope,
)
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.builder_session import BuilderSession
from app.models.organization import OrganizationMember
from app.models.skill import Skill
from app.models.skill_builder_session import SkillBuilderSession
from app.services.chat import conversations, messages

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase


@dataclass(frozen=True, slots=True)
class EmptyTree:
    nodes: tuple[()] = ()


pytest_plugins = ["tests.authz.test_private_agent_api_scope"]


@pytest.mark.parametrize("revoked", [False, True])
@pytest.mark.parametrize(
    "surface",
    [
        "actor",
        "messages",
        "conversation_actor",
        "agent_actor",
        "builder_actor",
        "skill_builder_actor",
        "skill_binding",
        "run_lock",
    ],
)
async def test_actor_guard_never_autoflushes_staged_private_resource(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    api_case: ApiCase,
    revoked: bool,
    surface: Literal[
        "actor",
        "messages",
        "conversation_actor",
        "agent_actor",
        "builder_actor",
        "skill_builder_actor",
        "skill_binding",
        "run_lock",
    ],
) -> None:
    own = private_scopes.first
    row = await conversations.create_conversation(db, api_case.first.agent_id, context=own)
    builder = BuilderSession(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        user_request="Dirty actor fixture",
    )
    skill_builder = SkillBuilderSession(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        user_request="Dirty actor fixture",
    )
    skill = Skill(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        slug="dirty-actor-fixture",
        name="Dirty actor fixture",
        kind="text",
    )
    db.add_all([builder, skill_builder, skill])
    await db.commit()
    if revoked:
        await db.execute(
            update(OrganizationMember)
            .where(
                OrganizationMember.user_id == own.user_id, OrganizationMember.org_id == own.org_id
            )
            .values(status="removed")
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    agent = await db.get(Agent, api_case.first.agent_id)
    assert agent is not None
    agent.name = "Never auto-flushed by private actor guard"
    flush = Mock()
    event.listen(db.sync_session, "before_flush", flush)
    denied_status: int | None = None
    try:
        match surface:
            case "actor":
                await private_actor.require_private_actor(db, own)
            case "messages":
                await messages.list_messages_from_checkpointer(
                    db, row, context=own, tree=EmptyTree()
                )
            case "conversation_actor":
                await conversation_scope.resolve_conversation_actor(db, row.id, own.user_id)
            case "agent_actor":
                await agent_scope.resolve_agent_actor(db, api_case.first.agent_id, own.user_id)
            case "builder_actor":
                await builder_scope.resolve_builder_actor(db, builder.id, own.user_id)
            case "skill_builder_actor":
                await skill_builder_scope.resolve_skill_builder_actor(
                    db, skill_builder.id, own.user_id
                )
            case "skill_binding":
                await skill_binding_scope.require_private_skill(db, skill, own)
            case "run_lock":
                target = conversation_run_scope.RunConversationTarget(
                    row.id, api_case.first.agent_id, own
                )
                if await conversation_run_scope.lock_run_conversation(db, target) is None:
                    denied_status = 404
            case unreachable:
                assert_never(unreachable)
    except AppError as error:
        denied_status = error.status
    finally:
        event.remove(db.sync_session, "before_flush", flush)
    staged = agent in db.dirty
    with db.no_autoflush:
        stored_name = await db.scalar(select(Agent.name).where(Agent.id == api_case.first.agent_id))
    written = stored_name == "Never auto-flushed by private actor guard"
    await db.rollback()
    assert denied_status in (403, 404) if revoked else denied_status is None
    assert flush.call_count == 0
    assert staged
    assert not written
