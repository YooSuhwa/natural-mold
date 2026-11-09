"""Workflows and their published versions (feature plan v2 순번 23, workflow-plan 3.1, W1).

* ``workflows``: a person's workflow in one organization (private, owner only in 1차 Q5):
  the editable draft definition with a revision counter (a stale save is a 409), the
  published version it runs, and ``archived_at``.
* ``workflow_versions``: what publishing froze; never edited. ``agent_pins`` holds each
  agent node's agent id and settings fingerprint (Q1): a run refuses an agent that changed.

Company rows (RLS ``DIRECT_TENANT``); a version goes with its workflow.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.resource_scope import RequiredOrgScope, resource_scope_constraints


def _now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Workflow(RequiredOrgScope, Base):
    __tablename__ = "workflows"
    __table_args__ = (
        *resource_scope_constraints("workflows", system=False),
        Index("ix_workflows_owner", "user_id", "org_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    draft_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # The version runs start from (``workflow_versions.version_no``); ``None``: unpublished.
    published_version_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=_now_naive, onupdate=_now_naive, nullable=False
    )


class WorkflowVersion(RequiredOrgScope, Base):
    __tablename__ = "workflow_versions"
    __table_args__ = (
        *resource_scope_constraints("workflow_versions", system=False),
        UniqueConstraint("workflow_id", "version_no", name="uq_workflow_versions_version_no"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # {node_id: {"agent_id": str, "fingerprint": str}} (Q1).
    agent_pins: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    published_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    published_at: Mapped[datetime] = mapped_column(default=_now_naive, nullable=False)


RUN_STATUSES = (
    "queued",
    "running",
    "waiting",
    "unknown",
    "completed",
    "failed",
    "canceling",
    "canceled",
)
RUN_TERMINAL = frozenset({"completed", "failed", "canceled"})
STEP_STATUSES = ("queued", "running", "waiting", "completed", "failed", "unknown", "canceled")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


class WorkflowRun(RequiredOrgScope, Base):
    """One run of a published version, started by ``user_id`` (the runner, workflow 3.6)."""

    __tablename__ = "workflow_runs"
    __table_args__ = (
        *resource_scope_constraints("workflow_runs", system=False),
        CheckConstraint(_in("status", RUN_STATUSES), name="ck_workflow_runs_status"),
        Index("ix_workflow_runs_workflow_created", "workflow_id", "created_at"),
        UniqueConstraint(
            "parent_conversation_id",
            "parent_tool_call_id",
            name="uq_workflow_runs_parent_tool_call",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_versions.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    input: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_node: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    # Started by an agent's workflow tool in chat (workflow-tool-plan 3.2): the conversation,
    # the tool call (the idempotency key: a resumed tool call finds its run again), the chat
    # run that called it, and when the chat was resumed with the result.
    parent_conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    parent_tool_call_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    parent_conversation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversation_runs.id", ondelete="SET NULL"), nullable=True
    )
    chat_resumed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=_now_naive, onupdate=_now_naive, nullable=False
    )


class WorkflowStepRun(RequiredOrgScope, Base):
    """One node of a run. For an agent node it is the run's business **envelope**: the
    accepted ``conversation_runs`` row binds here once (``conversation_run_id``)."""

    __tablename__ = "workflow_step_runs"
    __table_args__ = (
        *resource_scope_constraints("workflow_step_runs", system=False),
        CheckConstraint(_in("status", STEP_STATUSES), name="ck_workflow_step_runs_status"),
        # One row per node and loop round (workflow-loop-plan 3.3); earlier rounds are kept.
        UniqueConstraint("run_id", "node_id", "round", name="uq_workflow_step_runs_node_round"),
        CheckConstraint("round >= 1", name="ck_workflow_step_runs_round"),
        UniqueConstraint("conversation_run_id", name="uq_workflow_step_runs_conversation_run"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    round: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    node_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    input: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    branch: Mapped[str | None] = mapped_column(String(20), nullable=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    conversation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversation_runs.id", ondelete="SET NULL"), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now_naive, nullable=False)


__all__ = [
    "RUN_STATUSES",
    "RUN_TERMINAL",
    "STEP_STATUSES",
    "Workflow",
    "WorkflowRun",
    "WorkflowStepRun",
    "WorkflowVersion",
]
