"""Approval requests (feature plan v2 순번 13, chapter 17.2; ``app/approvals``).

One row per approval interrupt of a run: the run stopped before a tool that needs a
person's decision, and this row is that decision, wherever it is made (the chat's
approval card or the approval inbox). ``status`` moves away from ``pending`` exactly once
(compare-and-set in ``app.approvals.service.claim``), in the same transaction that queues
the resumed run, so two deciders can never resume a run twice.

What is kept: who ran it and who decides, the run and interrupt it belongs to, the
actions as the decider sees them (credential values already masked), the hash of the
original arguments (rechecked against the checkpoint right before the resume), the
policy version the run was pinned to and when it expires. Company rows (RLS
``DIRECT_TENANT``) that cascade with the company, the conversation and the run.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.organization_base import Identity, Timestamps

APPROVAL_STATUSES: Final = ("pending", "approved", "edited", "rejected", "expired", "canceled")
APPROVAL_SOURCES: Final = ("chat", "trigger", "api", "subagent", "workflow", "registration")
# ``tool``: a run paused before a tool call. ``business``: a workflow approval node (순번 23,
# workflow-plan 3.4), which has no conversation run: it points at the waiting step instead.
# ``registration``: a tool or MCP server waiting for an administrator (tool-governance-plan
# 3.7): no run either, it points at the resource and administrators decide it.
APPROVAL_KINDS: Final = ("tool", "business", "registration")
APPROVAL_CHANNELS: Final = ("chat", "inbox", "expiry", "emergency_stop")
_TABLE = "approval_requests"


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ",".join(f"'{value}'" for value in values) + ")"


STATUS_CHECK: Final = _in("status", APPROVAL_STATUSES)
SOURCE_CHECK: Final = _in("source", APPROVAL_SOURCES)
CHANNEL_CHECK: Final = "channel IS NULL OR " + _in("channel", APPROVAL_CHANNELS)
KIND_CHECK: Final = _in("kind", APPROVAL_KINDS)
SHAPE_CHECK: Final = (
    "(kind = 'tool' AND conversation_id IS NOT NULL AND run_id IS NOT NULL) OR "
    "(kind = 'business' AND workflow_step_run_id IS NOT NULL)"
    " OR (kind = 'registration' AND resource_type IS NOT NULL AND resource_id IS NOT NULL)"
)


class ApprovalRequest(Identity, Timestamps, Base):
    __tablename__ = _TABLE
    __table_args__ = (
        PrimaryKeyConstraint(name="pk_approval_requests"),
        UniqueConstraint("run_id", "interrupt_id", name="uq_approval_requests_run_interrupt"),
        CheckConstraint(STATUS_CHECK, name="ck_approval_requests_status"),
        CheckConstraint(SOURCE_CHECK, name="ck_approval_requests_source"),
        CheckConstraint(CHANNEL_CHECK, name="ck_approval_requests_channel"),
        CheckConstraint(KIND_CHECK, name="ck_approval_requests_kind"),
        CheckConstraint(SHAPE_CHECK, name="ck_approval_requests_shape"),
        Index("ix_approval_requests_decider_status", "decider_user_id", "status"),
        Index("ix_approval_requests_status_expires", "status", "expires_at"),
        Index("ix_approval_requests_conversation", "conversation_id"),
        Index("ix_approval_requests_resource", "resource_type", "resource_id"),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE", name="fk_approval_requests_tenant_id_tenants")
    )
    org_id: Mapped[UUID | None] = mapped_column(nullable=True)
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "conversations.id",
            ondelete="CASCADE",
            name="fk_approval_requests_conversation_id_conversations",
        )
    )
    run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "conversation_runs.id",
            ondelete="CASCADE",
            name="fk_approval_requests_run_id_conversation_runs",
        )
    )
    interrupt_id: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), default="tool", server_default="tool")
    workflow_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "workflow_runs.id",
            ondelete="CASCADE",
            name="fk_approval_requests_workflow_run_id_workflow_runs",
        )
    )
    workflow_step_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "workflow_step_runs.id",
            ondelete="CASCADE",
            name="fk_approval_requests_workflow_step_run_id_workflow_step_runs",
        )
    )
    namespace: Mapped[list[str]] = mapped_column(JSON, default=list)
    agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL", name="fk_approval_requests_agent_id_agents")
    )
    agent_name: Mapped[str] = mapped_column(String(200), default="")
    # Where the run came from (17.3) and, for a scheduled run, which schedule run waits.
    source: Mapped[str] = mapped_column(String(20))
    trigger_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "agent_triggers.id",
            ondelete="SET NULL",
            name="fk_approval_requests_trigger_id_agent_triggers",
        )
    )
    trigger_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "agent_trigger_runs.id",
            ondelete="SET NULL",
            name="fk_approval_requests_trigger_run_id_agent_trigger_runs",
        )
    )
    # The Agent API run that waits on this request (a deployment with the inbox policy).
    api_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "agent_api_runs.id",
            ondelete="SET NULL",
            name="fk_approval_requests_api_run_id_agent_api_runs",
        )
    )
    # ``registration`` requests: the resource waiting for an administrator (no FK: the
    # request outlives a deleted resource as history).
    resource_type: Mapped[str | None] = mapped_column(String(40))
    resource_id: Mapped[UUID | None] = mapped_column()
    # The run's subject (the person the run acts for) and the person who decides. Step 1
    # has no decider rules (17.2 "결정자"), so the decider is the subject.
    subject_user_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "users.id", ondelete="CASCADE", name="fk_approval_requests_subject_user_id_users"
        )
    )
    requester_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "users.id", ondelete="SET NULL", name="fk_approval_requests_requester_user_id_users"
        )
    )
    decider_user_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "users.id", ondelete="CASCADE", name="fk_approval_requests_decider_user_id_users"
        )
    )
    decided_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_approval_requests_decided_by_users")
    )
    # [{name, args, allowed_decisions, description}] as the decider sees them (masked).
    actions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    args_digest: Mapped[str] = mapped_column(String(64))
    edited_args_digest: Mapped[str | None] = mapped_column(String(64))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    policy_version: Mapped[int | None] = mapped_column(Integer)
    policy_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    channel: Mapped[str | None] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(String(200))
    decision_no: Mapped[int] = mapped_column(Integer, default=0)
    # The decision types as made, one per action (no arguments: those stay in the run).
    decisions: Mapped[list[str] | None] = mapped_column(JSON)
    resumed_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "conversation_runs.id",
            ondelete="SET NULL",
            name="fk_approval_requests_resumed_run_id_conversation_runs",
        )
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expiring_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
