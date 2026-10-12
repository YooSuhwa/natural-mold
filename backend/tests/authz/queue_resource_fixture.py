"""Actual queue writers and private file snapshots for retry authorization."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Unpack, assert_never
from uuid import uuid4

import pytest
from anyio import Path as AsyncPath
from sqlalchemy import delete, event, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run_input import ConversationRunInput, JsonValue
from app.models.message_attachment import MessageAttachment
from app.models.model import Model
from app.models.organization import OrganizationMember, TenantMember
from app.models.tool import Tool
from app.models.user import User
from app.schemas.agent import AgentCreate
from app.services import agent_service, conversation_run_queue_service
from app.services.chat.conversations import create_conversation
from app.services.chat_resource_context_integration import (
    QueuedResourceContextInput,
    freeze_resource_context_payload,
)
from app.services.chat_resource_context_sources import ResourceContextScope
from tests.authz.blueprint_preflight_fixture import SqlExecution

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

type Denial = Literal[
    "foreign_context",
    "wrong_company",
    "wrong_owner",
    "removed",
    "tenant_removed",
    "inactive_user",
    "moved_parent",
    "missing_parent",
    "scope_agent",
    "parent_agent",
    "orphan_parent",
    "child_agent",
    "child_owner",
    "request_parent",
    "moved_agent",
    "missing_agent",
]


@dataclass(frozen=True, slots=True)
class QueueResourceCase:
    scopes: PrivateScopes
    scope: ResourceContextScope
    request: QueuedResourceContextInput
    queued: ConversationRunInput
    path: Path
    snapshot: bytes


@pytest.fixture
async def queue_resource_case(
    db: AsyncSession, private_scopes: PrivateScopes, tmp_path: Path
) -> QueueResourceCase:
    own = private_scopes.first
    model = Model(provider="openai", model_name="queue-retry-fixture", display_name="Fixture")
    db.add(model)
    await db.flush()
    agent = await agent_service.create_agent(
        db,
        AgentCreate(name="Retry agent", system_prompt="Fixture", model_id=model.id),
        own,
    )
    conversation = await create_conversation(db, agent.id, context=own)
    stored = tmp_path / "retry-private.txt"
    body = "RETAINED_PRIVATE_QUEUE_SNAPSHOT"
    await AsyncPath(stored).write_text(body, encoding="utf-8")
    attachment = MessageAttachment(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        conversation_id=conversation.id,
        filename=stored.name,
        mime_type="text/plain",
        size_bytes=len(body.encode()),
        storage_path=str(stored),
        url="/fixture-retry",
    )
    db.add(attachment)
    await db.flush()
    scope = ResourceContextScope(own, agent.id, conversation.id)
    payload: dict[str, JsonValue] = {
        "messages": [{"role": "user", "content": "Repeat visible request"}],
        "resource_context": [{"kind": "file", "id": str(attachment.id)}],
    }
    request = QueuedResourceContextInput(conversation.id, "retained-retry", payload)
    frozen = await freeze_resource_context_payload(db, scope, payload)
    queued = await conversation_run_queue_service.enqueue_input(
        db,
        conversation_id=conversation.id,
        context=own,
        client_request_id=request.client_request_id,
        source="chat",
        input_payload=frozen,
        attachment_ids=[],
        checkpoint_id=None,
        priority=0,
    )
    await db.commit()
    return QueueResourceCase(private_scopes, scope, request, queued, stored, snapshot_bytes(frozen))


def snapshot_bytes(payload: Mapping[str, JsonValue]) -> bytes:
    """Canonical bytes make accidental snapshot refreshes visible."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def staged_queue_tool(case: QueueResourceCase) -> Tool:
    context = case.scope.context
    return Tool(
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        name="Legitimate staged queue control",
        definition_key="http_request",
    )


async def rejected_scope(
    db: AsyncSession, case: QueueResourceCase, denial: Denial
) -> QueueResourceCase:
    own, other = case.scopes.first, case.scopes.second
    match denial:
        case "foreign_context":
            return replace(case, scope=replace(case.scope, context=other))
        case "wrong_company":
            return replace(
                case, scope=replace(case.scope, context=replace(own, tenant_id=other.tenant_id))
            )
        case "wrong_owner":
            return replace(case, scope=replace(case.scope, context=replace(own, user_id=uuid4())))
        case "scope_agent":
            return replace(case, scope=replace(case.scope, agent_id=uuid4()))
        case "request_parent":
            return replace(case, scope=replace(case.scope, conversation_id=uuid4()))
        case "removed":
            await db.execute(
                update(OrganizationMember)
                .where(
                    OrganizationMember.org_id == own.org_id,
                    OrganizationMember.user_id == own.user_id,
                )
                .values(status="removed")
            )
        case "tenant_removed":
            await db.execute(
                update(TenantMember)
                .where(TenantMember.tenant_id == own.tenant_id, TenantMember.user_id == own.user_id)
                .values(status="removed")
            )
        case "inactive_user":
            await db.execute(update(User).where(User.id == own.user_id).values(is_active=False))
        case "moved_parent":
            await db.execute(
                update(Conversation)
                .where(Conversation.id == case.scope.conversation_id)
                .values(org_id=other.org_id, tenant_id=other.tenant_id)
                .execution_options(synchronize_session=False)
            )
        case "missing_parent":
            await db.execute(
                delete(Conversation)
                .where(Conversation.id == case.scope.conversation_id)
                .execution_options(synchronize_session=False)
            )
        case "moved_agent":
            await db.execute(
                update(Agent)
                .where(Agent.id == case.scope.agent_id)
                .values(org_id=other.org_id, tenant_id=other.tenant_id)
                .execution_options(synchronize_session=False)
            )
        case "missing_agent":
            await db.execute(
                delete(Agent)
                .where(Agent.id == case.scope.agent_id)
                .execution_options(synchronize_session=False)
            )
        case "parent_agent":
            await db.execute(
                update(Conversation)
                .where(Conversation.id == case.scope.conversation_id)
                .values(agent_id=uuid4())
                .execution_options(synchronize_session=False)
            )
        case "orphan_parent":
            await db.execute(
                update(Conversation)
                .where(Conversation.id == case.scope.conversation_id)
                .values(
                    agent_id=None,
                    agent_lifecycle_state="purged",
                    agent_purged_at=datetime.now(UTC),
                )
                .execution_options(synchronize_session=False)
            )
        case "child_agent":
            await db.execute(
                update(ConversationRunInput)
                .where(ConversationRunInput.id == case.queued.id)
                .values(agent_id=uuid4())
                .execution_options(synchronize_session=False)
            )
        case "child_owner":
            await db.execute(
                update(ConversationRunInput)
                .where(ConversationRunInput.id == case.queued.id)
                .values(user_id=uuid4())
                .execution_options(synchronize_session=False)
            )
        case unreachable:
            assert_never(unreachable)
    await db.commit()
    return case


@contextmanager
def queue_resource_sql(db: AsyncSession) -> Iterator[list[str]]:
    statements: list[str] = []

    def observe(**execution: Unpack[SqlExecution]) -> None:
        statements.append(execution["statement"])

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", observe, named=True)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", observe)
