"""A-13: groups, capabilities, invitations and SSO contracts (m79/m80)."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from schema_drafts.org_authz.base import DraftBase, Identity, OrgScope, Timestamps, org_fk


class Group(Identity, OrgScope, Timestamps, DraftBase):
    __tablename__ = "groups"
    __table_args__ = (
        org_fk(),
        UniqueConstraint("org_id", "name"),
        UniqueConstraint("id", "org_id"),
        CheckConstraint("source IN ('manual','idp')", name="source"),
    )
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20), server_default="manual")
    external_id: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class GroupMember(OrgScope, DraftBase):
    __tablename__ = "group_members"
    __table_args__ = (
        org_fk(),
        ForeignKeyConstraint(
            ["group_id", "org_id"], ["groups.id", "groups.org_id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["organization_members.org_id", "organization_members.user_id"],
            ondelete="CASCADE",
        ),
        Index("ix_group_members_user", "user_id", "org_id"),
    )
    group_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    added_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )


class OrgCapabilityGrant(Identity, OrgScope, DraftBase):
    __tablename__ = "org_capability_grants"
    __table_args__ = (
        org_fk(),
        UniqueConstraint("org_id", "capability", "principal_type", "principal_id"),
        CheckConstraint(
            "capability IN ('cap_create_agent','cap_create_skill','cap_create_tool',"
            "'cap_register_mcp','cap_connect_database','cap_share_org_wide',"
            "'cap_publish_marketplace')",
            name="capability",
        ),
        CheckConstraint("principal_type IN ('user','group','organization')", name="principal_type"),
        CheckConstraint(
            "principal_type != 'organization' OR principal_id = org_id",
            name="organization_principal",
        ),
    )
    capability: Mapped[str] = mapped_column(String(40))
    principal_type: Mapped[str] = mapped_column(String(20))
    principal_id: Mapped[UUID] = mapped_column()
    granted_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )


class OrgInvitation(Identity, OrgScope, DraftBase):
    __tablename__ = "org_invitations"
    __table_args__ = (
        org_fk(),
        CheckConstraint("role IN ('member','builder','admin','auditor')", name="role"),
        CheckConstraint("expires_at > created_at", name="expiration"),
        Index(
            "ix_org_invitations_pending",
            "org_id",
            "email",
            postgresql_where=text("accepted_at IS NULL AND revoked_at IS NULL"),
            sqlite_where=text("accepted_at IS NULL AND revoked_at IS NULL"),
        ),
    )
    email: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), server_default="member")
    group_ids: Mapped[list[str]] = mapped_column(JSON, server_default="[]")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invited_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
