"""Skill ORM — greenfield rewrite + marketplace lineage (ADR-017 m41/m42).

A ``Skill`` row describes a user-owned skill the agent runtime can mount.
The legacy ``content`` text column is gone — text-kind skills now persist
their body to ``storage_path`` (a single file on disk) just like package-kind
skills (a directory tree). This unifies the two storage paths and lets the
runtime stream large skills without round-tripping through Postgres.

ADR-017 (m41) adds 12 marketplace lineage columns + (m42) ``AgentSkillLink.config``
JSON for per-agent overrides (credential bindings today, parameter overrides
in future slices).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AgentSkillLink(Base):
    """Association: agent <-> skill.

    ``config`` (m42) carries agent-scoped overrides. Phase 1 uses it for
    credential bindings (`{"credential_bindings": {"<key>": "<credential-uuid>"}}`).
    """

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

    # m42 — JSON blob for agent-scoped overrides. Nullable; readers must
    # treat the missing/empty case as "no override".
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)

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

    # Filesystem location (ADR-018 — relative to ``settings.data_root``):
    #  - text:    ``skills/<id>/SKILL.md`` (single file).
    #  - package: ``skills/<id>`` (directory root).
    # Read sites must resolve via ``app.storage.paths.resolve_data_path``;
    # direct ``Path(value)`` use is a regression (absolute paths only land
    # here as M44 legacy fallback).
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

    # ---- m41 — marketplace lineage ---------------------------------------
    # System skills (k-skill seed, system_seed) are owned by no specific user
    # (CHECK enforced on tools/credentials; for skills we keep ``user_id``
    # NOT NULL so seed rows attach to the super_user account — see Spec §3.7).
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Where the skill came from: 'user' | 'k-skill' | 'import' | 'system_seed'.
    source_kind: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # When sourced from marketplace, the item + version that produced this
    # row. ON DELETE SET NULL so deleting an item doesn't cascade through
    # every installed copy.
    source_marketplace_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("marketplace_items.id", ondelete="SET NULL"), nullable=True
    )
    source_marketplace_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("marketplace_versions.id", ondelete="SET NULL"), nullable=True
    )
    # Upstream commit id (k-skill, git-imported).
    source_commit: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Snapshot of the version's credential_requirements at install time —
    # the runtime consults this without re-reading the marketplace row.
    credential_requirements: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Snapshot of execution_profile (support_level, runners, requires_*).
    execution_profile: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    current_revision_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    # Provenance from the *current owner's* perspective: who created the
    # row originally and where it was published. ``origin_kind`` drives
    # ``ResourceOriginSummaryOut.kind`` derivation.
    origin_kind: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="created_by_me",
        server_default="created_by_me",
    )
    origin_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    origin_marketplace_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("marketplace_items.id", ondelete="SET NULL"), nullable=True
    )
    origin_marketplace_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("marketplace_versions.id", ondelete="SET NULL"), nullable=True
    )
    # ``True`` when the installed row has been edited since installation —
    # surfaces in the installation summary and gates "update available" UX.
    is_dirty: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    # ---- timestamps ------------------------------------------------------
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
