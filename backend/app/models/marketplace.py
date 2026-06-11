"""Marketplace ORM (ADR-017 Slice A).

Single file holding all 6 marketplace-domain classes. ``SkillCredentialBinding``
is colocated here even though it strictly belongs to the ``skills`` table
group — keeping it adjacent to ``MarketplaceInstallation`` makes the
binding lifecycle obvious (install → bind → run).

Patterns (per ``docs/design-docs/marketplace-module-contracts.md``):

* SA 2.0 declarative + ``Mapped`` / ``mapped_column``.
* ``sa.Uuid()`` (portable) — Postgres backs it with ``UUID``, SQLite with
  ``CHAR(32)``. Tests use SQLite + ``Base.metadata.create_all``.
* CHECK constraints declared in ``__table_args__`` with explicit names so
  Alembic's autogenerate stays stable.
* Circular FK ``marketplace_items.latest_version_id`` is resolved via
  ``post_update=True`` on the relationship.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.agent import Agent
    from app.models.agent_blueprint import AgentBlueprint
    from app.models.credential import Credential
    from app.models.mcp_server import McpServer
    from app.models.skill import Skill
    from app.models.user import User


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# MarketplaceItem
# ---------------------------------------------------------------------------


class MarketplaceItem(Base):
    """Catalog entry — a single shareable resource shell.

    The ``latest_version_id`` column is filled after publish; the ALTER
    TABLE ADD CONSTRAINT in m40 closes the circular FK so deletions don't
    orphan rows. ``post_update=True`` on the relationship below is how
    SQLAlchemy handles the same loop at insert time.
    """

    __tablename__ = "marketplace_items"
    __table_args__ = (
        CheckConstraint(
            "resource_type IN ('agent','mcp','skill')",
            name="ck_marketplace_resource_type",
        ),
        CheckConstraint(
            "visibility IN ('private','restricted','public','unlisted','system')",
            name="ck_marketplace_visibility",
        ),
        CheckConstraint(
            "status IN ('draft','published','deprecated','disabled')",
            name="ck_marketplace_status",
        ),
        CheckConstraint(
            "(is_system = false) OR (owner_user_id IS NULL)",
            name="ck_marketplace_system_owner",
        ),
        Index(
            "ix_marketplace_items_listed",
            "is_listed",
            "visibility",
            "status",
        ),
        {"extend_existing": True},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    resource_type: Mapped[str] = mapped_column(String(20), nullable=False)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_listed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(220), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    icon_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    icon_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    visibility: Mapped[str] = mapped_column(String(20), nullable=False, default="private")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    moderation_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="approved"
    )

    source_kind: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_external_id: Mapped[str | None] = mapped_column(String(240), nullable=True)

    latest_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "marketplace_versions.id",
            ondelete="SET NULL",
            # SAWarning: circular FK between marketplace_items and
            # marketplace_versions. ``use_alter=True`` defers FK creation
            # until both tables exist (SQLite create_all + Alembic ALTER).
            use_alter=True,
            name="fk_marketplace_items_latest_version",
        ),
        nullable=True,
    )

    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    categories: Mapped[list | None] = mapped_column(JSON, nullable=True)
    locale: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Column name kept as ``metadata`` per Spec; attribute name renamed
    # because SQLAlchemy reserves ``metadata`` on the declarative base.
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships -----------------------------------------------------
    owner: Mapped[User | None] = relationship(
        "User", foreign_keys=[owner_user_id]
    )
    versions: Mapped[list[MarketplaceVersion]] = relationship(
        "MarketplaceVersion",
        back_populates="item",
        foreign_keys="MarketplaceVersion.item_id",
        cascade="all, delete-orphan",
    )
    latest_version: Mapped[MarketplaceVersion | None] = relationship(
        "MarketplaceVersion",
        foreign_keys=[latest_version_id],
        post_update=True,
    )
    acl_entries: Mapped[list[MarketplaceItemACL]] = relationship(
        "MarketplaceItemACL",
        back_populates="item",
        cascade="all, delete-orphan",
    )
    installations: Mapped[list[MarketplaceInstallation]] = relationship(
        "MarketplaceInstallation",
        back_populates="item",
        cascade="all, delete-orphan",
    )
    publication_links: Mapped[list[MarketplacePublicationLink]] = relationship(
        "MarketplacePublicationLink",
        back_populates="item",
        cascade="all, delete-orphan",
    )


# ---------------------------------------------------------------------------
# MarketplaceItemACL
# ---------------------------------------------------------------------------


class MarketplaceItemACL(Base):
    """Recipient list for ``visibility='restricted'`` items."""

    __tablename__ = "marketplace_item_acl"
    __table_args__ = (
        CheckConstraint(
            "permission IN ('view','install','manage')",
            name="ck_marketplace_acl_permission",
        ),
        {"extend_existing": True},
    )

    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("marketplace_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission: Mapped[str] = mapped_column(String(20), nullable=False, default="install")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    item: Mapped[MarketplaceItem] = relationship(
        "MarketplaceItem", back_populates="acl_entries"
    )
    user: Mapped[User] = relationship("User")


# ---------------------------------------------------------------------------
# MarketplaceVersion
# ---------------------------------------------------------------------------


class MarketplaceVersion(Base):
    """Immutable snapshot of an item's payload at publish time.

    Service layer treats every row as read-only after creation. The
    ``content_hash`` is the canonical SHA-256 of the packaged bytes —
    install reuses the snapshot directory directly when possible.
    """

    __tablename__ = "marketplace_versions"
    __table_args__ = (
        CheckConstraint(
            "resource_type IN ('agent','mcp','skill')",
            name="ck_marketplace_version_resource_type",
        ),
        CheckConstraint(
            "payload_kind IN ('skill_package','agent_spec','mcp_template')",
            name="ck_marketplace_payload_kind",
        ),
        UniqueConstraint(
            "item_id", "version_number", name="uq_marketplace_versions_item_number"
        ),
        Index("ix_marketplace_versions_content_hash", "content_hash"),
        {"extend_existing": True},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("marketplace_items.id", ondelete="CASCADE"), nullable=False
    )
    version_label: Mapped[str] = mapped_column(String(80), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    resource_type: Mapped[str] = mapped_column(String(20), nullable=False)
    payload_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # ADR-018 — relative to ``settings.data_root``. k-skill imports land at
    # ``marketplace/k-skill/<vid>``; user publishes land at
    # ``skills/_marketplace_versions/<vid>``. Read sites must resolve via
    # ``app.storage.paths.resolve_data_path``.
    storage_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    credential_requirements: Mapped[list | None] = mapped_column(JSON, nullable=True)
    dependency_requirements: Mapped[list | None] = mapped_column(JSON, nullable=True)
    execution_profile: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    release_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_commit: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    # Relationships -----------------------------------------------------
    item: Mapped[MarketplaceItem] = relationship(
        "MarketplaceItem",
        back_populates="versions",
        foreign_keys=[item_id],
    )
    installations: Mapped[list[MarketplaceInstallation]] = relationship(
        "MarketplaceInstallation",
        back_populates="version",
        foreign_keys="MarketplaceInstallation.version_id",
    )
    created_by_user: Mapped[User | None] = relationship(
        "User", foreign_keys=[created_by]
    )


# ---------------------------------------------------------------------------
# MarketplaceInstallation
# ---------------------------------------------------------------------------


class MarketplaceInstallation(Base):
    """Per-user installed resource pointer.

    The CHECK ``ck_marketplace_install_resource_target`` enforces that
    exactly one of the three ``installed_*_id`` columns is non-null and
    matches ``resource_type``.
    """

    __tablename__ = "marketplace_installations"
    __table_args__ = (
        CheckConstraint(
            "(resource_type = 'agent' "
            " AND ((installed_agent_blueprint_id IS NOT NULL "
            "       AND installed_agent_id IS NULL) "
            "      OR (installed_agent_id IS NOT NULL "
            "          AND installed_agent_blueprint_id IS NULL)) "
            " AND installed_mcp_server_id IS NULL AND installed_skill_id IS NULL) "
            "OR (resource_type = 'mcp' AND installed_mcp_server_id IS NOT NULL "
            " AND installed_agent_id IS NULL AND installed_agent_blueprint_id IS NULL "
            " AND installed_skill_id IS NULL) "
            "OR (resource_type = 'skill' AND installed_skill_id IS NOT NULL "
            " AND installed_agent_id IS NULL AND installed_agent_blueprint_id IS NULL "
            " AND installed_mcp_server_id IS NULL)",
            name="ck_marketplace_install_resource_target",
        ),
        CheckConstraint(
            "install_status IN ('active','needs_setup','disabled','uninstalled')",
            name="ck_marketplace_install_status",
        ),
        Index("ix_marketplace_install_user_item", "user_id", "item_id"),
        Index(
            "ix_marketplace_install_user_resource", "user_id", "resource_type"
        ),
        {"extend_existing": True},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("marketplace_items.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("marketplace_versions.id", ondelete="RESTRICT"), nullable=False
    )
    resource_type: Mapped[str] = mapped_column(String(20), nullable=False)
    installed_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=True
    )
    installed_agent_blueprint_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_blueprints.id", ondelete="CASCADE"), nullable=True
    )
    installed_mcp_server_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("mcp_servers.id", ondelete="CASCADE"), nullable=True
    )
    installed_skill_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"), nullable=True
    )
    install_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="active"
    )
    is_dirty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    installed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    # Relationships -----------------------------------------------------
    item: Mapped[MarketplaceItem] = relationship(
        "MarketplaceItem", back_populates="installations", foreign_keys=[item_id]
    )
    version: Mapped[MarketplaceVersion] = relationship(
        "MarketplaceVersion",
        back_populates="installations",
        foreign_keys=[version_id],
    )
    user: Mapped[User] = relationship("User", foreign_keys=[user_id])
    installed_skill: Mapped[Skill | None] = relationship(
        "Skill", foreign_keys=[installed_skill_id]
    )
    installed_agent: Mapped[Agent | None] = relationship(
        "Agent", foreign_keys=[installed_agent_id]
    )
    installed_agent_blueprint: Mapped[AgentBlueprint | None] = relationship(
        "AgentBlueprint", foreign_keys=[installed_agent_blueprint_id]
    )
    installed_mcp_server: Mapped[McpServer | None] = relationship(
        "McpServer", foreign_keys=[installed_mcp_server_id]
    )


# ---------------------------------------------------------------------------
# MarketplacePublicationLink
# ---------------------------------------------------------------------------


class MarketplacePublicationLink(Base):
    """Back-reference from a user-owned resource to their published item.

    UNIQUE on ``item_id`` because each marketplace item has at most one
    upstream source resource.
    """

    __tablename__ = "marketplace_publication_links"
    __table_args__ = (
        CheckConstraint(
            "resource_type IN ('agent','mcp','skill')",
            name="ck_pub_link_resource_type",
        ),
        CheckConstraint(
            "(resource_type = 'agent' AND source_agent_id IS NOT NULL "
            " AND source_mcp_server_id IS NULL AND source_skill_id IS NULL) "
            "OR (resource_type = 'mcp' AND source_mcp_server_id IS NOT NULL "
            " AND source_agent_id IS NULL AND source_skill_id IS NULL) "
            "OR (resource_type = 'skill' AND source_skill_id IS NOT NULL "
            " AND source_agent_id IS NULL AND source_mcp_server_id IS NULL)",
            name="ck_pub_link_target",
        ),
        UniqueConstraint("item_id", name="uq_pub_link_item"),
        Index("ix_pub_link_resource", "user_id", "resource_type"),
        {"extend_existing": True},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("marketplace_items.id", ondelete="CASCADE"), nullable=False
    )
    resource_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=True
    )
    source_mcp_server_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("mcp_servers.id", ondelete="CASCADE"), nullable=True
    )
    source_skill_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    item: Mapped[MarketplaceItem] = relationship(
        "MarketplaceItem", back_populates="publication_links"
    )
    user: Mapped[User] = relationship("User", foreign_keys=[user_id])


# ---------------------------------------------------------------------------
# SkillCredentialBinding
# ---------------------------------------------------------------------------


class SkillCredentialBinding(Base):
    """Per-user binding between a skill's credential_requirements[].key
    and a concrete ``credentials`` row.

    ON DELETE RESTRICT on ``credential_id`` to protect a live binding.
    """

    __tablename__ = "skill_credential_bindings"
    __table_args__ = (
        CheckConstraint(
            "scope IN ('skill','agent_skill')",
            name="ck_skill_credential_binding_scope",
        ),
        UniqueConstraint(
            "skill_id",
            "user_id",
            "requirement_key",
            "scope",
            name="uq_skill_credential_binding",
        ),
        {"extend_existing": True},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    requirement_key: Mapped[str] = mapped_column(String(120), nullable=False)
    credential_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("credentials.id", ondelete="RESTRICT"), nullable=False
    )
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="skill")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    skill: Mapped[Skill] = relationship("Skill", foreign_keys=[skill_id])
    credential: Mapped[Credential] = relationship(
        "Credential", foreign_keys=[credential_id]
    )
    user: Mapped[User] = relationship("User", foreign_keys=[user_id])


__all__ = [
    "MarketplaceItem",
    "MarketplaceItemACL",
    "MarketplaceVersion",
    "MarketplaceInstallation",
    "MarketplacePublicationLink",
    "SkillCredentialBinding",
]


# Cast for type checker — populate Any so attribute access doesn't shadow.
_ANY: Any = None
