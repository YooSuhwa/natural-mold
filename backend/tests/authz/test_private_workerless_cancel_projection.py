"""Independent real workerless cancellation recovery SQL after committed actor loss."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.message_event import MessageEvent
from app.models.organization import OrganizationMember, TenantMember
from app.models.user import User
from app.services import conversation_run_queue_service, conversation_run_service
from app.services.chat.conversations import create_conversation
from app.services.conversation_run_cancellation_recovery import (
    finalize_workerless_cancel_before_start,
)
from tests.authz.queue_resource_fixture import (
    queue_resource_case as queue_resource_case,
)
from tests.authz.queue_resource_fixture import (
    queue_resource_sql,
    snapshot_bytes,
)
from tests.conftest import TestSession

if TYPE_CHECKING:
    from tests.authz.queue_resource_fixture import QueueResourceCase
pytest_plugins = ["tests.authz.private_scope_fixture"]
pytestmark = pytest.mark.asyncio


@pytest.fixture
def _stub_llm_credential_resolution() -> None:
    """No authority, materializer or provider replacement."""


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
async def test_workerless_recovery_private_projection_contract(
    db: AsyncSession, queue_resource_case: QueueResourceCase, case: Case
) -> None:
    target = queue_resource_case
    own = target.scopes.first
    claimed = await conversation_run_queue_service.claim_next_input(
        db, conversation_id=target.scope.conversation_id, context=own
    )
    assert claimed is not None
    run_id, input_id = claimed.run.id, claimed.input.id
    revision = claimed.input.revision
    original_payload = snapshot_bytes(claimed.input.input_payload)
    original_attachments = tuple(claimed.input.attachment_ids)
    original_checkpoint = claimed.input.checkpoint_id
    await db.commit()
    async with TestSession() as writer:
        run = await writer.get(ConversationRun, run_id)
        assert run is not None
        await conversation_run_service.request_cancel_run(writer, run, reason="user")
        await writer.execute(
            update(User).where(User.id == own.user_id).values(is_super_user=case.operator)
        )
        match case.state:
            case "active":
                pass
            case "org_removed":
                await writer.execute(
                    update(OrganizationMember)
                    .where(
                        OrganizationMember.org_id == own.org_id,
                        OrganizationMember.user_id == own.user_id,
                    )
                    .values(status="removed")
                )
            case "tenant_removed":
                await writer.execute(
                    update(TenantMember)
                    .where(
                        TenantMember.tenant_id == own.tenant_id,
                        TenantMember.user_id == own.user_id,
                    )
                    .values(status="removed")
                )
            case "inactive":
                await writer.execute(
                    update(User).where(User.id == own.user_id).values(is_active=False)
                )
            case unreachable:
                assert_never(unreachable)
        await writer.commit()
    with queue_resource_sql(db) as sql:
        completed = await finalize_workerless_cancel_before_start(
            session_factory=TestSession, protected_run_ids=()
        )
    assert completed == [target.scope.conversation_id]
    async with TestSession() as verifier:
        row = await verifier.get(ConversationRun, run_id)
        queued = await verifier.get(ConversationRunInput, input_id)
        assert (
            row is not None
            and row.status == "canceled"
            and row.cancellation_acknowledged_at is not None
        )
        assert queued is not None and queued.status == "canceled" and queued.run_id is None
        assert queued.revision == revision + 1
        assert snapshot_bytes(queued.input_payload) == original_payload
        assert tuple(queued.attachment_ids) == original_attachments
        assert queued.checkpoint_id == original_checkpoint
        assert row.last_event_id == f"{run_id}-canceled"
        assert (
            await verifier.scalar(
                select(MessageEvent.id).where(
                    MessageEvent.assistant_msg_id == str(run_id),
                    MessageEvent.conversation_id == target.scope.conversation_id,
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
    private = [
        s
        for s in sql
        if s.lstrip().lower().startswith("select")
        and any(
            c in s.lower()
            for c in (
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
    assert private == [], (
        "Workerless recovery hydrates unused private Conversation/Run/Input payload"
    )


@pytest.mark.parametrize("variant", ["protected", "remote", "foreign_input"])
async def test_workerless_cancel_preserves_protected_remote_and_foreign_input(
    db: AsyncSession,
    queue_resource_case: QueueResourceCase,
    variant: Literal["protected", "remote", "foreign_input"],
) -> None:
    target = queue_resource_case
    own = target.scopes.first
    claimed = await conversation_run_queue_service.claim_next_input(
        db,
        conversation_id=target.scope.conversation_id,
        context=own,
    )
    assert claimed is not None
    run_id, input_id, revision = claimed.run.id, claimed.input.id, claimed.input.revision
    other = await create_conversation(db, target.scope.agent_id, context=own)
    await db.commit()
    async with TestSession() as writer:
        run = await writer.get(ConversationRun, run_id)
        assert run is not None
        await conversation_run_service.request_cancel_run(writer, run, reason="user")
        match variant:
            case "protected":
                pass
            case "remote":
                run.worker_instance_id = "worker-other-process"
            case "foreign_input":
                await writer.execute(
                    update(ConversationRunInput)
                    .where(
                        ConversationRunInput.id == input_id,
                    )
                    .values(conversation_id=other.id)
                )
            case unreachable:
                assert_never(unreachable)
        await writer.execute(
            update(OrganizationMember)
            .where(
                OrganizationMember.org_id == own.org_id,
                OrganizationMember.user_id == own.user_id,
            )
            .values(status="removed")
        )
        await writer.commit()
    protected = (run_id,) if variant == "protected" else ()
    completed = await finalize_workerless_cancel_before_start(
        session_factory=TestSession,
        protected_run_ids=protected,
    )
    assert completed == ([target.scope.conversation_id] if variant == "foreign_input" else [])
    async with TestSession() as verifier:
        run = await verifier.get(ConversationRun, run_id)
        queued = await verifier.get(ConversationRunInput, input_id)
        assert run is not None and queued is not None
        assert run.status == ("canceled" if variant == "foreign_input" else "canceling")
        assert (run.cancellation_acknowledged_at is not None) == (variant == "foreign_input")
        assert queued.status == "claimed" and queued.run_id == run_id
        assert queued.revision == revision
        assert queued.conversation_id == (
            other.id if variant == "foreign_input" else target.scope.conversation_id
        )
