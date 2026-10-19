"""Independent F1 persisted-target preflight witness on the frozen candidate."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal
from unittest.mock import Mock

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import conversation_scope
from app.authz.artifact_scope import validated_artifact_conversation
from app.exceptions import AppError
from app.models.agent import Agent
from app.services import share_service
from app.services.chat import conversations

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase
pytest_plugins = ["tests.authz.test_private_agent_api_scope"]


@pytest.mark.parametrize("foreign", [False, True])
@pytest.mark.parametrize("surface", ["conversation", "artifact", "share_revoke"])
async def test_target_preflight_does_not_write_before_foreign_denial(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    api_case: ApiCase,
    surface: Literal["conversation", "artifact", "share_revoke"],
    foreign: bool,
) -> None:
    own = private_scopes.first
    row = await conversations.create_conversation(db, api_case.first.agent_id, context=own)
    agent = await db.get(Agent, api_case.first.agent_id)
    assert agent is not None
    context = private_scopes.second if foreign else own
    agent.name = "F1 pending private content must stay staged before target denial"
    flush = Mock()
    event.listen(db.sync_session, "before_flush", flush)
    absent = False
    try:
        if surface == "conversation":
            absent = await conversation_scope.load_private_conversation(db, row.id, context) is None
        elif surface == "artifact":
            await validated_artifact_conversation(db, row.id, context)
        else:
            absent = await share_service.revoke_share(db, row.id, context) == []
    except AppError as error:
        assert error.status == 404
        absent = True
    finally:
        event.remove(db.sync_session, "before_flush", flush)
    with db.no_autoflush:
        stored = await db.scalar(select(Agent.name).where(Agent.id == agent.id))
    written = stored == "F1 pending private content must stay staged before target denial"
    await db.rollback()
    if foreign:
        assert absent
        assert flush.call_count == 0, "foreign target preflight emitted SQL mutation before denial"
        assert not written
    else:
        assert surface == "share_revoke" or not absent
