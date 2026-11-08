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

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text, UniqueConstraint
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


__all__ = ["Workflow", "WorkflowVersion"]
