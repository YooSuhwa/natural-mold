"""A private chat image, approved vision model and scoped credential for F07 tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.credential import Credential
from app.models.message_attachment import MessageAttachment
from app.models.model import Model
from app.models.organization import Organization
from app.services.chat import image_description_provider as provider
from tests.conftest import make_agent, make_model

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]

BASE_URL = "https://api.openai.com/v1"


def policy_settings(model_id: UUID) -> dict[str, JsonValue]:
    return {"model_id": str(model_id), "provider": "openai", "base_url": BASE_URL}


@dataclass
class Case:
    context: OrgContext
    org: Organization
    credential: Credential
    model: Model
    run: ConversationRun
    attachment: MessageAttachment

    def policy_settings(self) -> dict[str, JsonValue]:
        return policy_settings(self.model.id)

    async def resolve(
        self, db: AsyncSession, *, context: OrgContext | None = None, dispatch: bool = False
    ) -> provider.ImageDescriptionTarget:
        return await provider.resolve_image_description_target(
            db,
            context=context or self.context,
            run_id=self.run.id,
            conversation_id=self.run.conversation_id,
            attachment_id=self.attachment.id,
            require_running=dispatch,
        )


@pytest.fixture
async def case(db: AsyncSession, private_scopes: PrivateScopes) -> Case:
    context = private_scopes.first
    scope = {"user_id": context.user_id, "org_id": context.org_id, "tenant_id": context.tenant_id}
    credential = Credential(
        **scope,
        name="Vision",
        definition_key="openai",
        allowed_hosts=["api.openai.com"],
        data_encrypted="unused-test-ciphertext",
        key_id="fixture",
    )
    db.add(credential)
    await db.flush()
    model = await make_model(
        db,
        provider="openai",
        model_name="test-vision",
        supports_vision=True,
        base_url=BASE_URL,
        context_window=4096,
        max_output_tokens=1024,
        cost_per_input_token=Decimal("0.00001"),
        cost_per_output_token=Decimal("0.00002"),
        default_credential_id=credential.id,
    )
    root = await make_model(db, model_name="text-only", supports_vision=False)
    agent = await make_agent(
        db,
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        model_id=root.id,
    )
    conversation = Conversation(
        **scope,
        agent_id=agent.id,
        agent_name_snapshot=agent.name,
        last_activity_at=datetime.now(UTC),
    )
    db.add(conversation)
    await db.flush()
    run = ConversationRun(
        **scope,
        agent_id=agent.id,
        conversation_id=conversation.id,
        status="running",
        is_active=True,
        source="chat",
        run_source="CHAT",
        credential_subject_user_id=context.user_id,
        resource_owner_user_id=context.user_id,
        invoker_type="user",
    )
    attachment = MessageAttachment(
        **scope,
        conversation_id=conversation.id,
        filename="chart.png",
        mime_type="image/png",
        size_bytes=9,
        storage_path="chart.png",
        url="/api/attachments/chart.png",
    )
    db.add_all((run, attachment))
    org = await db.get(Organization, context.org_id)
    assert org is not None
    org.settings = {"image_description": policy_settings(model.id)}
    await db.commit()
    return Case(context, org, credential, model, run, attachment)
