"""Bounded frozen-source Agent denial and eager preparation witnesses."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal, assert_never
from unittest.mock import Mock

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.agent import Agent
from app.models.credential import Credential
from app.services import agent_service
from app.services.chat import runtime_context

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase

pytest_plugins = ["tests.authz.test_private_agent_api_scope"]


def test_production_source_is_the_dedicated_frozen_checkout() -> None:
    expected = Path(__file__).resolve().parents[2]
    assert Path(agent_service.__file__).is_relative_to(expected)
    assert Path(runtime_context.__file__).is_relative_to(expected)


@pytest.mark.parametrize("missing", [False, True])
async def test_agent_get_denies_before_sql_effects(
    db: AsyncSession, private_scopes: PrivateScopes, api_case: ApiCase, missing: bool
) -> None:
    from uuid import uuid4

    target = await db.get(Agent, api_case.second.agent_id)
    assert target is not None
    target_id = target.id
    original = target.name
    target.name = "F2 staged private content"
    flush = Mock()
    event.listen(db.sync_session, "before_flush", flush)
    try:
        result = await agent_service.get_agent(
            db, uuid4() if missing else target_id, private_scopes.first
        )
    finally:
        event.remove(db.sync_session, "before_flush", flush)
    with db.no_autoflush:
        stored = await db.scalar(select(Agent.name).where(Agent.id == target_id))
    staged = target in db.dirty
    await db.rollback()
    assert result is None
    assert flush.call_count == 0
    assert stored == original
    assert staged


@pytest.mark.parametrize("surface", ["agent", "conversation"])
async def test_runtime_eager_graph_denies_before_sql_effects(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    api_case: ApiCase,
    surface: Literal["agent", "conversation"],
) -> None:
    from app.authz.runtime_capability_scope import require_runtime_capability_tree
    from app.services.chat.conversations import create_conversation

    own, foreign = private_scopes.first, private_scopes.second
    target = await db.get(Agent, api_case.first.agent_id)
    assert target is not None
    credential = Credential(
        user_id=foreign.user_id,
        org_id=foreign.org_id,
        tenant_id=foreign.tenant_id,
        name="F2 foreign dummy credential",
        definition_key="openai_api",
        data_encrypted="dummy",
        key_id="dummy",
        field_keys=[],
    )
    db.add(credential)
    await db.commit()
    conversation = await create_conversation(db, target.id, context=own)
    target.llm_credential_id = credential.id
    await db.commit()
    pending = await db.get(Agent, api_case.second.agent_id)
    assert pending is not None
    original = pending.name
    pending.name = "F2 unrelated staged private content"
    flush = Mock()
    event.listen(db.sync_session, "before_flush", flush)
    denied = False
    try:
        match surface:
            case "agent":
                loaded = await runtime_context.get_agent_with_tools(db, target.id, own)
            case "conversation":
                row = await runtime_context.get_owned_conversation_with_agent(
                    db, conversation.id, own
                )
                assert row is not None
                loaded = row.agent
            case unreachable:
                assert_never(unreachable)
        assert loaded is not None
        require_runtime_capability_tree(loaded, own)
    except AppError as error:
        denied = error.status == 404
    finally:
        event.remove(db.sync_session, "before_flush", flush)
    with db.no_autoflush:
        stored = await db.scalar(select(Agent.name).where(Agent.id == pending.id))
    staged = pending in db.dirty
    await db.rollback()
    assert denied
    assert flush.call_count == 0
    assert stored == original
    assert staged
