"""Skill ORM — greenfield rewrite.

A ``Skill`` row describes a user-owned skill the agent runtime can mount.
The legacy ``content`` text column is gone — text-kind skills now persist
their body to ``storage_path`` (a single file on disk) just like package-kind
skills (a directory tree). This unifies the two storage paths and lets the
runtime stream large skills without round-tripping through Postgres.

Old columns dropped here (``content``): the m18 greenfield migration rebuilds
the physical table; tests use ``Base.metadata.create_all`` so the model is
the source of truth.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AgentSkillLink(Base):
    """Association: agent <-> skill."""

    __tablename__ = "agent_skills"
    __table_args__ = {"extend_existing": True}

    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"),
        primary_key=True,
    )

    skill: Mapped[Skill] = relationship(lazy="joined")


class Skill(Base):
    __tablename__ = "skills"
    __table_args__ = (
        UniqueConstraint("user_id", "slug", name="uq_skills_user_slug"),
        {"extend_existing": True},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    # URL-safe slug, unique per user. Used in storage paths.
    slug: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ``text`` | ``package``.
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="text", server_default="text"
    )

    # Filesystem location:
    #  - text:    a single file containing the body (e.g. ``<root>/<id>/SKILL.md``).
    #  - package: the extracted directory root.
    storage_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # SHA-256 of the canonical body (text: file bytes; package: SKILL.md bytes).
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # Optional version tag — package skills read it from SKILL.md frontmatter,
    # text skills accept a user-supplied label.
    version: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # Frontmatter / package metadata cache (e.g. ``{"name", "version", "files"}``).
    package_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=dict)

    # Cached count of agents currently linked to this skill (kept in sync by
    # service writes; rebuildable from agent_skills).
    used_by_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    last_modified_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )


SKILL_KINDS = ("text", "package")
