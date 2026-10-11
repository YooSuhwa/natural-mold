"""Secret collection cannot decrypt credentials from a same-owner foreign organization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.credentials import service as credential_service
from app.credentials.creation import CredentialCreation
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.organization import OrganizationMember, TenantMember
from app.models.tool import AgentToolLink, Tool
from app.models.user import User
from app.schemas.agent import AgentCreate
from app.services import agent_service
from app.services.chat.conversations import create_conversation
from app.services.chat.secrets import collect_conversation_secret_values

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


@dataclass(frozen=True, slots=True)
class CollectorCase:
    conversation: Conversation
    secret: str


@pytest.fixture
async def collector_case(db: AsyncSession, private_scopes: PrivateScopes) -> CollectorCase:
    model = Model(provider="openai", model_name="fixture", display_name="Fixture")
    db.add(model)
    await db.commit()
    agent = await agent_service.create_agent(
        db,
        AgentCreate(
            name="Collector fixture",
            system_prompt="Fixture",
            model_id=model.id,
            identity_mode="fixed",
        ),
        private_scopes.first,
    )
    foreign = await credential_service.create(
        db,
        CredentialCreation(
            definition_key="openai",
            name="Foreign collector fixture",
            data={"api_key": "dummy-foreign-collector"},
        ),
        private_scopes.second,
    )
    agent.llm_credential_id = foreign.id
    tool = Tool(
        user_id=private_scopes.first.user_id,
        org_id=private_scopes.first.org_id,
        tenant_id=private_scopes.first.tenant_id,
        name="Collector tool fixture",
        definition_key="http_request",
        credential_id=foreign.id,
    )
    db.add(tool)
    await db.flush()
    db.add(AgentToolLink(agent_id=agent.id, tool_id=tool.id))
    conversation = await create_conversation(db, agent.id, context=private_scopes.first)
    await db.commit()
    return CollectorCase(conversation, "dummy-foreign-collector")


async def test_foreign_bound_llm_and_tool_credential_are_not_decrypted(
    db: AsyncSession, collector_case: CollectorCase, private_scopes: PrivateScopes
) -> None:
    decrypt = AsyncMock(side_effect=AssertionError("Foreign secret must not be decrypted"))
    with patch.object(credential_service, "decrypt_with_external", decrypt):
        with pytest.raises(AppError) as captured:
            await collect_conversation_secret_values(
                db, collector_case.conversation, private_scopes.first
            )
        assert captured.value.status == 404
    decrypt.assert_not_awaited()


async def test_foreign_request_scope_is_rejected_before_secret_lookup(
    db: AsyncSession, collector_case: CollectorCase, private_scopes: PrivateScopes
) -> None:
    with pytest.raises(AppError) as captured:
        await collect_conversation_secret_values(
            db, collector_case.conversation, private_scopes.second
        )
    assert captured.value.status == 404


async def test_same_org_peer_agent_secret_is_not_collected_through_owned_conversation(
    db: AsyncSession, collector_case: CollectorCase, private_scopes: PrivateScopes
) -> None:
    # Given an owned conversation referencing another member's private Agent in the same org.
    scope = private_scopes.first
    peer = User(id=uuid4(), email="collector-peer@example.com", name="Peer")
    db.add(peer)
    await db.flush()
    db.add_all(
        [
            TenantMember(tenant_id=scope.tenant_id, user_id=peer.id),
            OrganizationMember(org_id=scope.org_id, tenant_id=scope.tenant_id, user_id=peer.id),
        ]
    )
    await db.flush()
    peer_context = OrgContext(user_id=peer.id, org_id=scope.org_id, tenant_id=scope.tenant_id)
    own_agent = await db.get(Agent, collector_case.conversation.agent_id)
    assert own_agent is not None
    credential = await credential_service.create(
        db,
        CredentialCreation(
            definition_key="openai",
            name="Peer fixture",
            data={"api_key": "dummy-peer-collector-secret"},
        ),
        peer_context,
    )
    peer_agent = await agent_service.create_agent(
        db,
        AgentCreate(
            name="Peer", system_prompt="Fixture", model_id=own_agent.model_id, identity_mode="fixed"
        ),
        peer_context,
    )
    peer_agent.llm_credential_id = credential.id
    collector_case.conversation.agent_id = peer_agent.id
    await db.commit()
    # When read-time redaction collects resources for the conversation's direct owner.
    secrets = await collect_conversation_secret_values(db, collector_case.conversation, scope)
    # Then another member's private credential is never read through the malformed link.
    assert "dummy-peer-collector-secret" not in secrets


async def test_collector_rejects_foreign_tool_before_owned_llm_decryption(
    db: AsyncSession, collector_case: CollectorCase, private_scopes: PrivateScopes
) -> None:
    own = private_scopes.first
    credential = await credential_service.create(
        db, CredentialCreation("openai", "Owned dummy", {"api_key": "B06_DUMMY_ONLY"}), own
    )
    agent = await db.get(Agent, collector_case.conversation.agent_id)
    assert agent is not None
    agent.llm_credential_id = credential.id
    await db.commit()
    db.expunge_all()
    decrypt = AsyncMock(wraps=credential_service.decrypt_with_external)
    with pytest.MonkeyPatch.context() as patches:
        patches.setattr(credential_service, "decrypt_with_external", decrypt)
        with pytest.raises(AppError) as captured:
            await collect_conversation_secret_values(db, collector_case.conversation, own)
        assert captured.value.status == 404
        decrypt.assert_not_awaited()
