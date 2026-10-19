"""Actual m83 immutable queue retries recheck current membership before private effects."""

from pathlib import Path
from uuid import UUID

import pytest
from anyio import Path as AsyncPath
from fastapi import HTTPException
from sqlalchemy import select, update

from app.authz.conversation_scope import resolve_conversation_actor
from app.authz.org_context import resolve_org_context
from app.config import settings
from app.dependencies import CurrentUser
from app.models.message_attachment import MessageAttachment
from app.models.model import Model
from app.models.organization import OrganizationMember
from app.models.tool import Tool
from app.models.user import User
from app.schemas.agent import AgentCreate
from app.services import agent_service
from app.services import conversation_run_queue_service as queue
from app.services.chat.conversations import create_conversation
from app.services.chat_resource_context_integration import (
    QueuedResourceContextInput,
    freeze_queued_resource_context_payload,
    freeze_resource_context_payload,
)
from app.services.chat_resource_context_sources import ResourceContextScope
from tests.authz.queue_resource_fixture import queue_resource_sql, snapshot_bytes
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("revoked", [False, True])
async def test_native_queue_retry_preserves_snapshot_and_rejects_revoked_actor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, revoked: bool
) -> None:
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    async with signup_app(monkeypatch) as stack:
        response = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-resource-retry@example.com",
                "name": "Retry fixture",
                "password": "native-resource-retry-password",
            },
        )
        assert response.status_code == 201, response.text
        user_id = UUID(response.json()["user"]["id"])
        async with stack.sessions() as db:
            user = await db.get(User, user_id)
            assert user is not None
            context = await resolve_org_context(
                db, CurrentUser(id=user.id, email=user.email, name=user.name), cookie=None
            )
            model = Model(provider="openai", model_name="native-resource", display_name="Fixture")
            db.add(model)
            await db.flush()
            agent = await agent_service.create_agent(
                db,
                AgentCreate(name="Native retry", system_prompt="Fixture", model_id=model.id),
                context,
            )
            conversation = await create_conversation(db, agent.id, context=context)
            context = await resolve_conversation_actor(db, conversation.id, user_id)
            path = tmp_path / "private-native-retry.txt"
            body = "ORIGINAL_PRIVATE_NATIVE_RETRY"
            await AsyncPath(path).write_text(body)
            attachment = MessageAttachment(
                user_id=user_id,
                org_id=context.org_id,
                tenant_id=context.tenant_id,
                conversation_id=conversation.id,
                filename=path.name,
                mime_type="text/plain",
                size_bytes=len(body),
                storage_path=str(path),
                url="/native-fixture",
            )
            db.add(attachment)
            await db.flush()
            scope = ResourceContextScope(context, agent.id, conversation.id)
            request = QueuedResourceContextInput(
                conversation.id,
                "native-retry",
                {
                    "messages": [{"role": "user", "content": "Repeat native request"}],
                    "resource_context": [{"kind": "file", "id": str(attachment.id)}],
                },
            )
            frozen = await freeze_resource_context_payload(db, scope, request.input_payload)
            queued = await queue.enqueue_input(
                db,
                context=context,
                conversation_id=conversation.id,
                client_request_id=request.client_request_id,
                source="chat",
                input_payload=frozen,
                attachment_ids=[],
                checkpoint_id=None,
                priority=0,
            )
            await db.commit()
            expected = snapshot_bytes(frozen)
            await AsyncPath(path).write_text("UPDATED_CONTENT_MUST_NOT_REPLACE_ACCEPTED_SNAPSHOT")
            if revoked:
                async with stack.sessions() as writer:
                    await writer.execute(
                        update(OrganizationMember)
                        .where(
                            OrganizationMember.org_id == context.org_id,
                            OrganizationMember.user_id == user_id,
                        )
                        .values(status="removed")
                    )
                    await writer.commit()
            pending = Tool(
                user_id=user_id,
                org_id=context.org_id,
                tenant_id=context.tenant_id,
                name="Native retry staged write",
                definition_key="http_request",
            )
            db.add(pending)
            with queue_resource_sql(db) as statements:
                if revoked:
                    with pytest.raises(HTTPException) as denied:
                        await freeze_queued_resource_context_payload(db, scope, request)
                    assert denied.value.status_code == 404
                    assert pending in db.new
                    assert not any(
                        sql.lower().lstrip().startswith(("insert ", "update ", "delete "))
                        for sql in statements
                    )
                    assert not any(
                        "from conversations" in sql.lower()
                        or "conversation_run_inputs.input_payload" in sql.lower()
                        or "message_attachments" in sql.lower()
                        for sql in statements
                    )
                else:
                    result = await freeze_queued_resource_context_payload(db, scope, request)
                    assert snapshot_bytes(result) == expected
                    accepted = await queue.enqueue_input(
                        db,
                        context=context,
                        conversation_id=conversation.id,
                        client_request_id=request.client_request_id,
                        source="chat",
                        input_payload=result,
                        attachment_ids=[],
                        checkpoint_id=None,
                        priority=0,
                    )
                    assert accepted.id == queued.id
                    await db.flush()
                    assert (
                        await db.scalar(select(Tool.id).where(Tool.id == pending.id)) == pending.id
                    )
