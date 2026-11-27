"""One accepted handoff from a chat run to a separately resumable child conversation."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.organization_base import Created, Identity, org_fk


class IndependentChatJob(Identity, Created, Base):
    __tablename__ = "independent_chat_jobs"
    __table_args__ = (
        PrimaryKeyConstraint(name="pk_independent_chat_jobs"),
        org_fk("independent_chat_jobs", "CASCADE"),
        # Bind all three identities to immutable accepted participants. Agent purge
        # clears the live participant.agent_id while retaining its snapshot.
        ForeignKeyConstraint(
            ["parent_run_id", "parent_agent_id"],
            ["conversation_run_agents.run_id", "conversation_run_agents.agent_id_snapshot"],
            name="fk_independent_chat_jobs_parent_root",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["parent_run_id", "child_agent_id"],
            ["conversation_run_agents.run_id", "conversation_run_agents.agent_id_snapshot"],
            name="fk_independent_chat_jobs_parent_delegate",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["child_run_id", "child_agent_id"],
            ["conversation_run_agents.run_id", "conversation_run_agents.agent_id_snapshot"],
            name="fk_independent_chat_jobs_child_root",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "parent_conversation_id <> child_conversation_id AND parent_run_id <> child_run_id",
            name="ck_independent_chat_jobs_distinct_execution",
        ),
        UniqueConstraint("parent_run_id", "request_id", name="uq_independent_chat_jobs_request"),
        UniqueConstraint(
            "child_conversation_id", name="uq_independent_chat_jobs_child_conversation"
        ),
        UniqueConstraint("child_run_id", name="uq_independent_chat_jobs_child_run"),
        Index(
            "uq_independent_chat_jobs_source_task",
            "parent_run_id",
            "source_child_task_id",
            unique=True,
            sqlite_where=text("source_child_task_id IS NOT NULL"),
            postgresql_where=text("source_child_task_id IS NOT NULL"),
        ),
        Index(
            "ix_independent_chat_jobs_pending_approval",
            "pending_approval_run_id",
            sqlite_where=text("pending_approval_run_id IS NOT NULL"),
            postgresql_where=text("pending_approval_run_id IS NOT NULL"),
        ),
        Index("ix_independent_chat_jobs_parent", "parent_conversation_id", "created_at"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", name="fk_independent_chat_jobs_tenant", ondelete="CASCADE")
    )
    org_id: Mapped[UUID] = mapped_column()
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", name="fk_independent_chat_jobs_owner", ondelete="CASCADE")
    )
    parent_agent_id: Mapped[UUID] = mapped_column()
    parent_run_id: Mapped[UUID] = mapped_column()
    parent_conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "conversations.id",
            name="fk_independent_chat_jobs_parent_conversation",
            ondelete="CASCADE",
        )
    )
    child_agent_id: Mapped[UUID] = mapped_column()
    child_run_id: Mapped[UUID] = mapped_column()
    child_conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "conversations.id",
            name="fk_independent_chat_jobs_child_conversation",
            ondelete="CASCADE",
        )
    )
    request_id: Mapped[UUID] = mapped_column()
    request_digest: Mapped[str] = mapped_column(String(64))
    capability_sha256: Mapped[str] = mapped_column(String(64))
    source_child_task_id: Mapped[UUID | None] = mapped_column()
    pending_approval_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "conversation_runs.id",
            name="fk_independent_chat_jobs_pending_approval",
            ondelete="SET NULL",
        )
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
