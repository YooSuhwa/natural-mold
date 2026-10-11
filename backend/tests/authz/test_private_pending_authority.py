"""Independent R15 pending-insert and staged authority/child preflight witnesses."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import agent_scope, conversation_scope, private_actor, skill_builder_scope
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.organization import OrganizationMember
from app.models.skill_builder_session import SkillBuilderSession
from app.schemas.skill_builder_scope import SkillBuilderToolTarget
from app.services.chat.conversations import create_conversation
from app.storage.skill_draft_paths import skill_draft_storage_path

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase
pytest_plugins = ["tests.authz.test_private_agent_api_scope"]


@pytest.mark.parametrize("revoked", [False, True])
@pytest.mark.parametrize("surface", ["captured", "agent", "conversation"])
async def test_pending_new_content_stays_unflushed_during_actor_preflight(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    api_case: ApiCase,
    revoked: bool,
    surface: Literal["captured", "agent", "conversation"],
) -> None:
    own = private_scopes.first
    row = await create_conversation(db, api_case.first.agent_id, context=own)
    source = await db.get(Agent, api_case.first.agent_id)
    assert source is not None
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
    staged = Agent(
        id=uuid4(),
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="Pending independent private content",
        system_prompt="never published",
        model_id=source.model_id,
        runtime_name="pending-" + uuid4().hex[:20],
    )
    db.add(staged)
    flush = Mock()
    event.listen(db.sync_session, "before_flush", flush)
    denied = False
    denied_status: int | None = None
    try:
        match surface:
            case "captured":
                await private_actor.require_private_actor(db, own)
            case "agent":
                await agent_scope.resolve_agent_actor(db, source.id, own.user_id)
            case "conversation":
                await conversation_scope.resolve_conversation_actor(db, row.id, own.user_id)
            case unreachable:
                assert_never(unreachable)
    except AppError as error:
        denied_status = error.status
        denied = True
    finally:
        event.remove(db.sync_session, "before_flush", flush)
    with db.no_autoflush:
        stored = await db.scalar(
            select(func.count()).select_from(Agent).where(Agent.id == staged.id)
        )
    remains_new = staged in db.new
    await db.rollback()
    assert denied == revoked
    assert denied_status in (403, 404) if revoked else denied_status is None
    assert flush.call_count == 0
    assert stored == 0
    assert remains_new


@pytest.mark.parametrize("direct", [False, True])
async def test_staged_regrant_does_not_override_persisted_revocation(
    db: AsyncSession, private_scopes: PrivateScopes, direct: bool
) -> None:
    own = private_scopes.first
    member = await db.scalar(
        select(OrganizationMember).where(
            OrganizationMember.user_id == own.user_id, OrganizationMember.org_id == own.org_id
        )
    )
    assert member is not None
    member.status = "removed"
    await db.commit()
    member.status = "active"
    flush = Mock()
    event.listen(db.sync_session, "before_flush", flush)
    try:
        if direct:
            with pytest.raises(AppError) as denied:
                await private_actor.resolve_private_actor(
                    db, private_actor.PrivateResourceActor(own.user_id, own.org_id, own.tenant_id)
                )
        else:
            with pytest.raises(AppError) as denied:
                await private_actor.require_private_actor(db, own)
        assert denied.value.status in (403, 404)
    finally:
        event.remove(db.sync_session, "before_flush", flush)
    with db.no_autoflush:
        stored = await db.scalar(
            select(OrganizationMember.status).where(
                OrganizationMember.user_id == own.user_id, OrganizationMember.org_id == own.org_id
            )
        )
    await db.rollback()
    assert flush.call_count == 0
    assert stored == "removed"


@pytest.mark.parametrize("missing", [False, True])
async def test_invalid_skill_builder_tool_agent_denies_before_pending_sql(
    db: AsyncSession, private_scopes: PrivateScopes, api_case: ApiCase, missing: bool
) -> None:
    own = private_scopes.first
    source = await db.get(Agent, api_case.first.agent_id)
    assert source is not None
    session = SkillBuilderSession(
        id=uuid4(),
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        user_request="Independent child preflight",
    )
    workspace_path = skill_draft_storage_path(session.id)
    session.draft_workspace_path = workspace_path
    db.add(session)
    await db.commit()
    staged = Agent(
        id=uuid4(),
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="Do not flush before denied capability",
        system_prompt="private pending content",
        model_id=source.model_id,
        runtime_name="pending-" + uuid4().hex[:20],
    )
    db.add(staged)
    target = SkillBuilderToolTarget(
        session.id, own, workspace_path, uuid4() if missing else api_case.second.agent_id
    )
    flush = Mock()
    event.listen(db.sync_session, "before_flush", flush)
    try:
        with pytest.raises(AppError) as denied:
            await skill_builder_scope.require_skill_builder_tool_session(db, target)
        assert denied.value.status == 404
    finally:
        event.remove(db.sync_session, "before_flush", flush)
    with db.no_autoflush:
        stored = await db.scalar(
            select(func.count()).select_from(Agent).where(Agent.id == staged.id)
        )
    remains_new = staged in db.new
    await db.rollback()
    assert flush.call_count == 0
    assert stored == 0
    assert remains_new
