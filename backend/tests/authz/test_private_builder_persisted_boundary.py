"""Independent F1 persisted-root and private-writer adversarial witnesses."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, assert_never
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.credentials.creation import CredentialCreation
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.organization import OrganizationMember
from app.models.skill_builder_session import SkillBuilderSession
from app.models.tool import Tool
from app.services import builder_service, skill_builder_service
from app.services.chat import secrets as secrets_module
from app.services.skill_builder_errors import SkillBuilderValidationError

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_secret_collector import CollectorCase

pytest_plugins = ["tests.authz.test_private_secret_collector"]


async def test_collector_rechecks_persisted_conversation_root_before_decrypt(
    db: AsyncSession, private_scopes: PrivateScopes, collector_case: CollectorCase
) -> None:
    own, foreign = private_scopes.first, private_scopes.second
    credential = await credential_service.create(
        db, CredentialCreation("openai", "Owned dummy", {"api_key": "B06_DUMMY_ONLY"}), own
    )
    agent = await db.get(Agent, collector_case.conversation.agent_id)
    assert agent is not None
    agent.llm_credential_id = credential.id
    await db.execute(update(Tool).values(credential_id=credential.id))
    await db.commit()
    conversation = collector_case.conversation
    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation.id)
        .values(org_id=foreign.org_id, tenant_id=foreign.tenant_id)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    assert conversation.org_id == own.org_id
    persisted = (
        await db.execute(select(Conversation.org_id).where(Conversation.id == conversation.id))
    ).scalar_one()
    assert persisted == foreign.org_id
    decrypt = AsyncMock(wraps=credential_service.decrypt_with_external)
    denied = False
    with pytest.MonkeyPatch.context() as patches:
        patches.setattr(credential_service, "decrypt_with_external", decrypt)
        try:
            await secrets_module.collect_conversation_secret_values(db, conversation, own)
        except AppError:
            denied = True
    assert denied and decrypt.await_count == 0


@pytest.mark.parametrize("writer", ["builder", "skill_builder"])
async def test_session_creation_rechecks_captured_membership_before_durable_write(
    db: AsyncSession, private_scopes: PrivateScopes, writer: Literal["builder", "skill_builder"]
) -> None:
    own = private_scopes.first
    await db.execute(
        delete(OrganizationMember).where(
            OrganizationMember.org_id == own.org_id, OrganizationMember.user_id == own.user_id
        )
    )
    await db.commit()
    denied = False
    try:
        match writer:
            case "builder":
                await builder_service.create_session(db, own, "Revoked fixture")
            case "skill_builder":
                await skill_builder_service.create_session(
                    db, context=own, user_request="Revoked fixture"
                )
            case unreachable:
                assert_never(unreachable)
        await db.commit()
    except AppError:
        denied = True
    assert denied, f"{writer} wrote a private session after captured membership was removed"


async def test_confirmation_rechecks_foreign_conversation_edge_before_validation_write(
    db: AsyncSession, private_scopes: PrivateScopes
) -> None:
    own, foreign = private_scopes.first, private_scopes.second
    conversation = Conversation(
        user_id=foreign.user_id,
        org_id=foreign.org_id,
        tenant_id=foreign.tenant_id,
        agent_id=None,
        agent_name_snapshot="Retained fixture",
        last_activity_at=datetime.now(UTC),
        title="Foreign retained fixture",
    )
    db.add(conversation)
    await db.flush()
    session = await skill_builder_service.create_session(
        db, context=own, user_request="Owned malformed parent fixture"
    )
    session.conversation_id = conversation.id
    session.draft_package = {
        "name": "Fixture",
        "slug": "fixture",
        "description": "Fixture",
        "files": [],
    }
    await db.commit()
    denied = False
    validation_reached = False
    try:
        await skill_builder_service.confirm_session(db, session, context=own)
    except AppError:
        denied = True
    except SkillBuilderValidationError:
        validation_reached = True
    await db.commit()
    stored = (
        await db.execute(
            select(SkillBuilderSession.validation_result, SkillBuilderSession.status).where(
                SkillBuilderSession.id == session.id
            )
        )
    ).one()
    assert denied and stored.validation_result is None and not validation_reached
