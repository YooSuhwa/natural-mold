"""Actual m83 current actors precede private history and queue source reads."""

from dataclasses import dataclass
from typing import Literal, assert_never
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import select, update

from app.authz.conversation_scope import resolve_conversation_actor
from app.authz.org_context import resolve_org_context
from app.config import settings
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.organization import OrganizationMember
from app.models.tool import Tool
from app.models.user import User
from app.schemas.agent import AgentCreate
from app.services import agent_service
from app.services import conversation_run_queue_service as queue
from app.services.chat import conversations, messages
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
            async with stack.sessions() as writer:
                match action:
                    case "messages":
                        await writer.execute(
                            update(Conversation)
                            .where(Conversation.id == conversation.id)
                            .values(agent_id=None)
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
                            assert result == [] and conversation.agent_id is None
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
                    for sql in statements
                )
            else:
                assert queued.priority == 7 and queued.position == 1 and queued.revision == 1
                match action:
                    case "messages":
                        assert source.accesses == ["nodes"]
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
