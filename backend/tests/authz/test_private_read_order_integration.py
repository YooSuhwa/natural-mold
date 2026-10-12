"""Current-schema actors precede private history and queue source reads."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, assert_never
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_scope import resolve_conversation_actor
from app.authz.org_context import resolve_org_context
from app.config import settings
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.conversation import Conversation
from app.models.conversation_history import (
    ConversationHistoryCheckpoint,
    ConversationHistoryManifest,
)
from app.models.model import Model
from app.models.organization import OrganizationMember
from app.models.tool import Tool
from app.models.user import User
from app.schemas.agent import AgentCreate
from app.schemas.conversation_history import JsonValue
from app.services import agent_service
from app.services import conversation_run_queue_service as queue
from app.services.agent_purge_history_validation import verify_history_manifest
from app.services.chat import conversations, messages
from app.services.conversation_history_projection import canonical_payload_sha256
from tests.authz.queue_resource_fixture import queue_resource_sql
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration

type NativeRead = Literal["messages", "queue"]


@dataclass(frozen=True, slots=True)
class HistorySource:
    """The mutable accumulator records reads of the supplied history source."""

    accesses: list[str]

    @property
    def nodes(self) -> tuple[()]:
        self.accesses.append("nodes")
        return ()


async def _seed_retained_message(
    db: AsyncSession, conversation_id: UUID, source_agent_id: UUID
) -> str:
    checkpoint_id = "native-read-order-retained"
    retained_content = "RETAINED_READ_ORDER_CONTENT"
    checkpoint_values: dict[str, JsonValue] = {"source": "retained-history"}
    checkpoint_messages: list[JsonValue] = [
        {"id": "retained-read-message", "type": "human", "content": retained_content}
    ]
    checkpoint_branch: dict[str, JsonValue] = {"active": True}
    checkpoint_trace: dict[str, JsonValue] = {"references": []}
    checkpoint_payload: dict[str, JsonValue] = {
        "checkpoint_id": checkpoint_id,
        "parent_checkpoint_id": None,
        "checkpoint_namespace": "",
        "channel_values": checkpoint_values,
        "messages": checkpoint_messages,
        "branch": checkpoint_branch,
        "trace": checkpoint_trace,
    }
    checkpoint_sha256 = canonical_payload_sha256(checkpoint_payload)
    manifest_payload: JsonValue = {
        "schema_version": 1,
        "conversation_id": str(conversation_id),
        "source_agent_id": str(source_agent_id),
        "checkpoints": [{**checkpoint_payload, "payload_sha256": checkpoint_sha256}],
        "events": [],
    }
    manifest = ConversationHistoryManifest(
        conversation_id=conversation_id,
        source_agent_id_snapshot=source_agent_id,
        state="complete",
        checkpoint_count=1,
        event_count=0,
        canonical_payload_sha256=canonical_payload_sha256(manifest_payload),
    )
    db.add(manifest)
    await db.flush()
    db.add(
        ConversationHistoryCheckpoint(
            manifest_id=manifest.id,
            conversation_id=conversation_id,
            checkpoint_id=checkpoint_id,
            parent_checkpoint_id=None,
            checkpoint_namespace="",
            channel_values=checkpoint_values,
            messages=checkpoint_messages,
            branch=checkpoint_branch,
            trace=checkpoint_trace,
            payload_sha256=checkpoint_sha256,
        )
    )
    await db.flush()
    assert await verify_history_manifest(db, manifest)
    return retained_content


@pytest.mark.parametrize(
    "case",
    [
        (action, operator, revoked)
        for action in ("messages", "queue")
        for operator in (False, True)
        for revoked in (False, True)
    ],
)
async def test_native_current_actor_precedes_private_content_and_preserves_normal_writes(
    monkeypatch: pytest.MonkeyPatch, case: tuple[NativeRead, bool, bool]
) -> None:
    action, operator, revoked = case
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    monkeypatch.setattr(settings, "allow_first_user_as_admin", operator)
    async with signup_app(monkeypatch) as stack:
        response = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-read-order@example.com",
                "name": "Read order fixture",
                "password": "native-read-order-fixture-password",
            },
        )
        assert response.status_code == 201, response.text
        user_id = UUID(response.json()["user"]["id"])
        async with stack.sessions() as db:
            user = await db.get(User, user_id)
            assert user is not None and user.is_active
            assert user.is_super_user is operator
            context = await resolve_org_context(
                db,
                CurrentUser(id=user.id, email=user.email, name=user.name, is_super_user=operator),
                cookie=None,
            )
            model = Model(provider="openai", model_name="read-order", display_name="Fixture")
            db.add(model)
            await db.flush()
            agent = await agent_service.create_agent(
                db,
                AgentCreate(name="Read order", system_prompt="Fixture", model_id=model.id),
                context,
            )
            conversation = await conversations.create_conversation(db, agent.id, context=context)
            context = await resolve_conversation_actor(db, conversation.id, user_id)
            queued = await queue.enqueue_input(
                db,
                context=context,
                conversation_id=conversation.id,
                client_request_id="native-read-order",
                source="chat",
                input_payload={"messages": [{"role": "user", "content": "Native fixture"}]},
                attachment_ids=[],
                checkpoint_id=None,
                priority=7,
            )
            await db.commit()
            retained_content = None
            async with stack.sessions() as writer:
                match action:
                    case "messages":
                        retained_content = await _seed_retained_message(
                            writer, conversation.id, agent.id
                        )
                        await writer.execute(
                            update(Conversation)
                            .where(Conversation.id == conversation.id)
                            .values(
                                agent_id=None,
                                agent_lifecycle_state="purged",
                                agent_purged_at=datetime.now(UTC),
                            )
                        )
                        if revoked:
                            await writer.execute(
                                update(User).where(User.id == user_id).values(is_active=False)
                            )
                    case "queue":
                        if revoked:
                            await writer.execute(
                                update(OrganizationMember)
                                .where(
                                    OrganizationMember.org_id == context.org_id,
                                    OrganizationMember.user_id == user_id,
                                )
                                .values(status="removed")
                            )
                    case unreachable:
                        assert_never(unreachable)
                await writer.commit()
            pending = Tool(
                user_id=user_id,
                org_id=context.org_id,
                tenant_id=context.tenant_id,
                name="Native accepted caller write",
                definition_key="http_request",
            )
            db.add(pending)
            source = HistorySource([])
            accepted = False
            with queue_resource_sql(db) as statements:
                try:
                    match action:
                        case "messages":
                            result = await messages.list_messages_from_checkpointer(
                                db, conversation, context=context, tree=source
                            )
                            assert retained_content is not None
                            assert len(result) == 1 and result[0].content == retained_content
                            assert result[0].branch_checkpoint_id == "native-read-order-retained"
                            assert conversation.agent_id is None
                        case "queue":
                            inputs = await queue.list_inputs(
                                db, conversation_id=conversation.id, context=context
                            )
                            assert [item.id for item in inputs] == [queued.id]
                        case unreachable:
                            assert_never(unreachable)
                    accepted = True
                except (AppError, HTTPException) as error:
                    match error:
                        case AppError(status=status):
                            assert status == 404
                        case HTTPException(status_code=status):
                            assert status == 404
                        case unreachable:
                            assert_never(unreachable)
            assert accepted is not revoked
            if revoked:
                assert pending in db.new
                assert source.accesses == []
                assert not any(
                    sql.lower().lstrip().startswith(("insert ", "update ", "delete "))
                    for sql in statements
                )
                assert not any(
                    "conversations.runtime_policy_snapshot" in sql.lower()
                    or "conversations.title" in sql.lower()
                    or "conversation_run_inputs.input_payload" in sql.lower()
                    or "conversation_history_manifests" in sql.lower()
                    or "conversation_history_checkpoints" in sql.lower()
                    or "conversation_history_events" in sql.lower()
                    for sql in statements
                )
            else:
                assert queued.priority == 7 and queued.position == 1 and queued.revision == 1
                match action:
                    case "messages":
                        assert source.accesses == []
                        assert any(
                            "conversation_history_manifests" in sql.lower() for sql in statements
                        )
                        assert any(
                            "conversation_history_checkpoints" in sql.lower() for sql in statements
                        )
                    case "queue":
                        assert source.accesses == []
                    case unreachable:
                        assert_never(unreachable)
                await db.commit()
                async with stack.sessions() as verifier:
                    assert (
                        await verifier.scalar(select(Tool.id).where(Tool.id == pending.id))
                        == pending.id
                    )
