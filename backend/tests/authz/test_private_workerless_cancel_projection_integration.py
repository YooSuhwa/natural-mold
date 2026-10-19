"""Actual m83 workerless cancellation keeps ACK and avoids private payload reads."""

from dataclasses import dataclass
from typing import Literal, assert_never
from uuid import UUID

import pytest
from sqlalchemy import select, update

from app.authz.org_context import resolve_org_context
from app.config import settings
from app.dependencies import CurrentUser
from app.models.audit_event import AuditEvent
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.message_event import MessageEvent
from app.models.model import Model
from app.models.organization import OrganizationMember, TenantMember
from app.models.user import User
from app.schemas.agent import AgentCreate
from app.services import agent_service, conversation_run_queue_service, conversation_run_service
from app.services.chat.conversations import create_conversation
from app.services.conversation_run_cancellation_recovery import (
    finalize_workerless_cancel_before_start,
)
from tests.authz.queue_resource_fixture import queue_resource_sql, snapshot_bytes
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration


@dataclass(frozen=True, slots=True)
class Case:
    state: Literal["active", "org_removed", "tenant_removed", "inactive"]
    operator: bool


@pytest.mark.parametrize(
    "case",
    [
        Case(s, o)
        for s in ("active", "org_removed", "tenant_removed", "inactive")
        for o in (False, True)
    ],
)
async def test_native_workerless_cancel_projection_and_durable_ack(
    monkeypatch: pytest.MonkeyPatch,
    case: Case,
) -> None:
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    monkeypatch.setattr(settings, "allow_first_user_as_admin", case.operator)
    async with signup_app(monkeypatch) as stack:
        response = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-workerless@example.com",
                "name": "Workerless fixture",
                "password": "native-workerless-fixture-password",
            },
        )
        assert response.status_code == 201, response.text
        user_id = UUID(response.json()["user"]["id"])
        async with stack.sessions() as db:
            user = await db.get(User, user_id)
            assert user is not None and user.is_super_user is case.operator
            context = await resolve_org_context(
                db,
                CurrentUser(
                    id=user.id,
                    email=user.email,
                    name=user.name,
                    is_super_user=case.operator,
                ),
                cookie=None,
            )
            model = Model(
                provider="openai", model_name="workerless-fixture", display_name="Fixture"
            )
            db.add(model)
            await db.flush()
            agent = await agent_service.create_agent(
                db,
                AgentCreate(
                    name="Workerless",
                    system_prompt="Private workerless fixture",
                    model_id=model.id,
                ),
                context,
            )
            conversation = await create_conversation(db, agent.id, context=context)
            queued = await conversation_run_queue_service.enqueue_input(
                db,
                conversation_id=conversation.id,
                context=context,
                client_request_id="native-workerless",
                source="chat",
                input_payload={
                    "messages": [{"role": "user", "content": "PRIVATE_WORKERLESS_PAYLOAD"}]
                },
                attachment_ids=[],
                checkpoint_id=None,
                priority=7,
            )
            claimed = await conversation_run_queue_service.claim_next_input(
                db,
                conversation_id=conversation.id,
                context=context,
            )
            assert claimed is not None and claimed.input.id == queued.id
            run_id, input_id, revision = claimed.run.id, claimed.input.id, claimed.input.revision
            before = snapshot_bytes(claimed.input.input_payload)
            await db.commit()
            async with stack.sessions() as writer:
                run = await writer.get(ConversationRun, run_id)
                assert run is not None
                await conversation_run_service.request_cancel_run(writer, run, reason="user")
                match case.state:
                    case "active":
                        pass
                    case "org_removed":
                        await writer.execute(
                            update(OrganizationMember)
                            .where(
                                OrganizationMember.org_id == context.org_id,
                                OrganizationMember.user_id == user_id,
                            )
                            .values(status="removed")
                        )
                    case "tenant_removed":
                        await writer.execute(
                            update(TenantMember)
                            .where(
                                TenantMember.tenant_id == context.tenant_id,
                                TenantMember.user_id == user_id,
                            )
                            .values(status="removed")
                        )
                    case "inactive":
                        await writer.execute(
                            update(User).where(User.id == user_id).values(is_active=False)
                        )
                    case unreachable:
                        assert_never(unreachable)
                await writer.commit()
            with queue_resource_sql(db) as statements:
                completed = await finalize_workerless_cancel_before_start(
                    session_factory=stack.sessions,
                    protected_run_ids=(),
                )
            assert completed == [conversation.id]
            private = [
                s
                for s in statements
                if s.lstrip().lower().startswith("select")
                and any(
                    field in s.lower()
                    for field in (
                        "conversations.title",
                        "conversations.runtime_policy_snapshot",
                        "conversation_runs.input_preview",
                        "conversation_runs.metadata_json",
                        "conversation_run_inputs.input_payload",
                        "conversation_run_inputs.attachment_ids",
                        "conversation_run_inputs.checkpoint_id",
                    )
                )
            ]
            assert private == []
            async with stack.sessions() as verifier:
                run = await verifier.get(ConversationRun, run_id)
                item = await verifier.get(ConversationRunInput, input_id)
                assert run is not None and item is not None
                assert run.status == "canceled" and run.cancellation_acknowledged_at is not None
                assert run.cancel_reason == "user" and run.worker_instance_id is None
                assert run.last_event_id == f"{run_id}-canceled"
                assert (
                    item.status == "canceled"
                    and item.run_id is None
                    and item.revision == revision + 1
                )
                assert snapshot_bytes(item.input_payload) == before
                assert item.priority == 7 and item.client_request_id == "native-workerless"
                assert (
                    await verifier.scalar(
                        select(MessageEvent.id).where(
                            MessageEvent.assistant_msg_id == str(run_id),
                            MessageEvent.conversation_id == conversation.id,
                        )
                    )
                    is not None
                )
                assert (
                    await verifier.scalar(
                        select(AuditEvent.id).where(
                            AuditEvent.run_id == str(run_id),
                            AuditEvent.action == "conversation.run_canceled",
                        )
                    )
                    is not None
                )
