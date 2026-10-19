"""A malformed builder-to-conversation edge cannot grant session consent."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.routers.conversation_agent_protocol_consent import apply_session_consent_decisions
from app.routers.session_consent_scope import SessionConsentPayload, SessionConsentTarget
from app.services import skill_builder_service, skill_draft_workspace
from app.storage.skill_draft_paths import skill_draft_storage_path
from tests.test_skill_builder_consent import _pending, _resume

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_secret_collector import CollectorCase

pytest_plugins = ["tests.authz.test_private_secret_collector"]


async def test_consent_denies_foreign_session_before_draft_read_and_mutation(
    db: AsyncSession, collector_case: CollectorCase, private_scopes: PrivateScopes
) -> None:
    session = await skill_builder_service.create_session(
        db, context=private_scopes.second, user_request="Foreign consent fixture"
    )
    session.conversation_id = collector_case.conversation.id
    session.draft_workspace_path = skill_draft_storage_path(session.id)
    await db.commit()
    target = SessionConsentTarget(collector_case.conversation.id, private_scopes.first)
    payload = SessionConsentPayload(
        _resume([{"type": "approve", "scope": "session"}]), _pending(["test_skill_draft"])
    )
    with pytest.MonkeyPatch.context() as patches:
        read = Mock(return_value=False)
        patches.setattr(skill_draft_workspace, "draft_requires_network", read)
        recorded = await apply_session_consent_decisions(db, target, payload)
        assert recorded == []
        read.assert_not_called()
    await db.commit()
    await db.refresh(session)
    assert not session.tool_consents
