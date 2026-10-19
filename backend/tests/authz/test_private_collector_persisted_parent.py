"""Check read-time decryption against the persisted Conversation root and Agent edge."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.credentials.creation import CredentialCreation
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.tool import Tool
from app.schemas.agent import AgentCreate
from app.services import agent_service
from app.services.chat.secrets import collect_conversation_secret_values

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_secret_collector import CollectorCase

pytest_plugins = ["tests.authz.test_private_secret_collector"]


@pytest.mark.parametrize("edge", ["root_scope", "agent_parent"])
async def test_collector_refreshes_conversation_before_decryption(
    db: AsyncSession,
    collector_case: CollectorCase,
    private_scopes: PrivateScopes,
    edge: Literal["root_scope", "agent_parent"],
) -> None:
    own, foreign = private_scopes.first, private_scopes.second
    credential = await credential_service.create(
        db, CredentialCreation("openai", "Owned fixture", {"api_key": "B06_CACHED_ROOT_DUMMY"}), own
    )
    conversation = collector_case.conversation
    agent = await db.get(Agent, conversation.agent_id)
    assert agent is not None
    agent.llm_credential_id = credential.id
    await db.execute(update(Tool).values(credential_id=credential.id))
    await db.commit()
    # Genuine owned control exercises the actual collector, not a mocked return.
    control = await collect_conversation_secret_values(db, conversation, own)
    assert "B06_CACHED_ROOT_DUMMY" in control
    with pytest.raises(AppError):
        await collect_conversation_secret_values(db, conversation, foreign)
    # Persist a malformed root/parent without modifying the cached Conversation.
    match edge:
        case "root_scope":
            mutation = update(Conversation).values(
                org_id=foreign.org_id, tenant_id=foreign.tenant_id
            )
        case "agent_parent":
            foreign_agent = await agent_service.create_agent(
                db,
                AgentCreate(
                    name="Foreign fixture", system_prompt="Fixture", model_id=agent.model_id
                ),
                foreign,
            )
            mutation = update(Conversation).values(agent_id=foreign_agent.id)
        case unreachable:
            assert_never(unreachable)
    await db.execute(
        mutation.where(Conversation.id == conversation.id).execution_options(
            synchronize_session=False
        )
    )
    await db.commit()
    assert conversation.org_id == own.org_id and conversation.agent_id == agent.id
    persisted = (
        await db.execute(
            select(Conversation.org_id, Conversation.tenant_id, Conversation.agent_id).where(
                Conversation.id == conversation.id
            )
        )
    ).one()
    match edge:
        case "root_scope":
            assert persisted.org_id == foreign.org_id and persisted.tenant_id == foreign.tenant_id
        case "agent_parent":
            assert persisted.agent_id != conversation.agent_id
        case unreachable:
            assert_never(unreachable)
    decrypt = AsyncMock(wraps=credential_service.decrypt_with_external)
    collected: set[str] = set()
    with pytest.MonkeyPatch.context() as patches:
        patches.setattr(credential_service, "decrypt_with_external", decrypt)
        try:
            collected = await collect_conversation_secret_values(db, conversation, own)
        except AppError:
            collected = set()
    assert decrypt.await_count == 0 and not collected
