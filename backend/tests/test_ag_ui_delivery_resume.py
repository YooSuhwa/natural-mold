from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_config import AgentConfig
from app.approvals import service as approvals
from app.authz.org_context import OrgContext
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.approval_request import ApprovalRequest
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.conversation_run import ConversationRun
from app.services import ag_ui_delivery_resume as delivery
from tests.authz.g03_support import seed_conversation
from tests.authz.test_independent_job_authority import delegation as delegation
from tests.authz.unit_scope_fixture import ensure_unit_scope
from tests.test_independent_job_approvals import Delegation, pause_job

pytestmark = pytest.mark.asyncio
INTERRUPT = "approval-1"
CHECKPOINT = "checkpoint-1"
VALUE = {
    "action_requests": [{"name": "publish", "args": {"document": "v2"}}],
    "review_configs": [{"action_name": "publish", "allowed_decisions": ["approve", "reject"]}],
}


@dataclass
class Accepted:
    conversation: Conversation
    parent: ConversationRun
    run: ConversationRun
    context: OrgContext
    cfg: AgentConfig
    row: ApprovalRequest
    payload: dict[str, Any]

    async def prepare(self, db: AsyncSession) -> object:
        return await delivery.prepare_resume_delivery(
            db,
            run=self.run,
            context=self.context,
            conversation=self.conversation,
            cfg=self.cfg,
            checkpoint_id=CHECKPOINT,
            payload=self.payload,
        )


def _cfg(conversation: Conversation, context: OrgContext) -> AgentConfig:
    return AgentConfig(
        provider="openai",
        model_name="unit",
        api_key=None,
        base_url=None,
        system_prompt="",
        tools_config=[],
        thread_id=str(conversation.id),
        org_context=context,
        checkpoint_id=CHECKPOINT,
        independent_job=conversation.source == INDEPENDENT_JOB_SOURCE,
    )


def _child(conversation: Conversation, parent: ConversationRun) -> ConversationRun:
    return ConversationRun(
        id=uuid4(),
        parent_run_id=parent.id,
        source="resume",
        status="queued",
        interrupt_id=INTERRUPT,
        is_active=True,
        tenant_id=conversation.tenant_id,
        org_id=conversation.org_id,
        user_id=conversation.user_id,
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        run_source="CHAT",
    )


@pytest.fixture
async def accepted(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> Accepted:
    conversation = await seed_conversation(db)
    context = await ensure_unit_scope(
        db,
        CurrentUser(
            id=conversation.user_id,
            name="Reviewer",
            email="reviewer@example.test",
            is_super_user=False,
        ),
    )
    parent = ConversationRun(
        id=uuid4(),
        source="chat",
        status="interrupted",
        is_active=False,
        tenant_id=context.tenant_id,
        org_id=context.org_id,
        user_id=context.user_id,
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        run_source="TRIGGER",
        interrupt_id=INTERRUPT,
    )
    db.add(parent)
    await db.flush()
    [row] = await approvals.record_pending(
        db, parent, [approvals.PendingInterrupt(INTERRUPT, VALUE)], origin=approvals.RunOrigin()
    )
    child = _child(conversation, parent)
    db.add(child)
    await db.flush()
    await approvals.claim(
        db,
        approvals.Claim(
            run_id=parent.id,
            decisions={INTERRUPT: ["approve"]},
            channel="chat",
            actor_id=context.user_id,
            resumed_run_id=child.id,
        ),
    )
    await db.commit()
    payload = {
        "interruptIds": [INTERRUPT],
        "response": {INTERRUPT: {"decisions": [{"type": "approve"}]}},
        "approvalRequestIds": {INTERRUPT: str(row.id)},
    }
    monkeypatch.setattr(
        delivery,
        "read_pending_checkpoint",
        AsyncMock(
            return_value=SimpleNamespace(
                checkpoint_id=CHECKPOINT,
                interrupts=[{"id": INTERRUPT, "value": VALUE, "ns": []}],
                expires_at={INTERRUPT: row.expires_at},
                native_kinds={INTERRUPT: "approval"},
                approval_run_ids={INTERRUPT: (parent.id,)},
            )
        ),
    )
    return Accepted(conversation, parent, child, context, _cfg(conversation, context), row, payload)


async def test_delivery_uses_claim_without_incrementing_decision(
    db: AsyncSession,
    accepted: Accepted,
) -> None:
    assert await accepted.prepare(db) == accepted.payload["response"]
    assert accepted.cfg.run_path == "trigger"
    assert accepted.row.decision_no == 1


@pytest.mark.parametrize(
    "change",
    [
        "deleted",
        "expired",
        "namespace",
        "digest",
        "actor",
        "resumed_run",
        "answer",
        "goal_restriction",
        "wrong_checkpoint",
        "cancelled",
    ],
)
async def test_current_evidence_must_still_match(
    db: AsyncSession,
    accepted: Accepted,
    change: str,
) -> None:
    if change == "deleted":
        await db.delete(accepted.row)
    elif change == "expired":
        accepted.row.expires_at = approvals.now() - timedelta(seconds=1)
    elif change == "namespace":
        accepted.row.namespace = ["sibling"]
    elif change == "digest":
        accepted.row.args_digest = approvals.digest("another action")
    elif change == "actor":
        accepted.row.decided_by = None
    elif change == "resumed_run":
        accepted.row.resumed_run_id = accepted.parent.id
    elif change == "answer":
        accepted.payload["response"][INTERRUPT]["decisions"] = [{"type": "reject"}]
    elif change == "goal_restriction":
        accepted.parent.metadata_json = {delivery.INPUT_KEY: str(uuid4())}
    elif change == "wrong_checkpoint":
        accepted.cfg.checkpoint_id = "another-checkpoint"
    else:
        accepted.run.cancel_requested_at = approvals.now()
    await db.commit()
    with pytest.raises(AppError, match="no longer matches"):
        await accepted.prepare(db)


async def test_expiry_claim_can_only_deliver_a_rejection(
    db: AsyncSession,
    accepted: Accepted,
) -> None:
    row = accepted.row
    row.channel, row.status, row.reason = "expiry", "expired", "approval_expired"
    row.expires_at = approvals.now() - timedelta(minutes=1)
    row.decisions = ["reject"]
    await db.commit()
    with pytest.raises(AppError):
        await accepted.prepare(db)
    accepted.payload["response"][INTERRUPT]["decisions"] = [{"type": "reject"}]
    assert await accepted.prepare(db) == accepted.payload["response"]


async def test_cancelled_independent_job_does_not_materialize_approval(
    db: AsyncSession,
    delegation: Delegation,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, _, child, job = delegation
    await pause_job(db, child, job)
    conversation = await db.get(Conversation, child.conversation_id)
    assert conversation is not None
    successor = _child(conversation, child)
    db.add(successor)
    job.cancel_requested_at = approvals.now()
    await db.commit()
    from app.services import ag_ui_pending_checkpoint

    read = AsyncMock()
    monkeypatch.setattr(ag_ui_pending_checkpoint, "conversation_checkpointer", read)
    with pytest.raises(AppError) as exc:
        await delivery.prepare_resume_delivery(
            db,
            run=successor,
            conversation=conversation,
            context=context,
            cfg=_cfg(conversation, context),
            checkpoint_id=CHECKPOINT,
            payload={
                "interruptIds": [INTERRUPT],
                "response": {INTERRUPT: {}},
                "approvalRequestIds": {},
            },
        )
    assert exc.value.code == "STALE_INTERRUPT"
    read.assert_not_awaited()


async def test_inherited_approval_remains_bound_after_legacy_partial_resume(
    db: AsyncSession,
    accepted: Accepted,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = ConversationRun(
        id=uuid4(),
        source="start",
        status="interrupted",
        is_active=False,
        org_id=accepted.context.org_id,
        tenant_id=accepted.context.tenant_id,
        user_id=accepted.context.user_id,
        conversation_id=accepted.conversation.id,
        agent_id=accepted.run.agent_id,
        run_source="CHAT",
        interrupt_id=INTERRUPT,
    )
    db.add(previous)
    await db.flush()
    accepted.parent.parent_run_id = previous.id
    accepted.parent.source = "resume"
    [earlier] = await approvals.record_pending(
        db, previous, [approvals.PendingInterrupt(INTERRUPT, VALUE)], origin=approvals.RunOrigin()
    )
    await approvals.claim(
        db,
        approvals.Claim(
            run_id=previous.id,
            decisions={INTERRUPT: ["approve"]},
            channel="chat",
            actor_id=accepted.context.user_id,
            resumed_run_id=accepted.run.id,
        ),
    )
    await db.commit()
    accepted.payload["inheritedApprovalRequestIds"] = {INTERRUPT: [str(earlier.id)]}
    monkeypatch.setattr(
        delivery,
        "read_pending_checkpoint",
        AsyncMock(
            return_value=SimpleNamespace(
                checkpoint_id=CHECKPOINT,
                interrupts=[{"id": INTERRUPT, "value": VALUE, "ns": []}],
                expires_at={INTERRUPT: earlier.expires_at},
                native_kinds={INTERRUPT: "approval"},
                approval_run_ids={INTERRUPT: (previous.id, accepted.parent.id)},
            )
        ),
    )
    assert await accepted.prepare(db) == accepted.payload["response"]
    await db.delete(earlier)
    await db.commit()
    with pytest.raises(AppError, match="no longer matches"):
        await accepted.prepare(db)


async def test_edited_argument_digest_survives_recovery(
    db: AsyncSession,
    accepted: Accepted,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.ag_ui_pending_checkpoint import PendingCheckpoint

    value = {
        "action_requests": VALUE["action_requests"],
        "review_configs": [{"action_name": "publish", "allowed_decisions": ["edit"]}],
    }
    monkeypatch.setattr(
        delivery,
        "read_pending_checkpoint",
        AsyncMock(
            return_value=PendingCheckpoint(
                CHECKPOINT,
                [{"id": INTERRUPT, "value": value, "ns": []}],
                {INTERRUPT: accepted.row.expires_at},
                approval_run_ids={INTERRUPT: (accepted.parent.id,)},
            )
        ),
    )
    edited_action = {"name": "publish", "args": {"document": "reviewed-v3"}}
    accepted.row.status, accepted.row.decisions = "edited", ["edit"]
    accepted.row.edited_args_digest = approvals.digest([edited_action])
    await db.commit()
    accepted.payload["response"][INTERRUPT] = {
        "decisions": [{"type": "edit", "edited_action": edited_action}]
    }
    assert await accepted.prepare(db) == accepted.payload["response"]
    edited_action["args"]["document"] = "unreviewed-v4"
    with pytest.raises(AppError, match="no longer matches"):
        await accepted.prepare(db)
