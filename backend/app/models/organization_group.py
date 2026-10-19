"""Tenant organization models promoted from the reviewed m78–m80 contract (groups)."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.organization_base import Identity, Timestamps, org_fk


class Group(Identity, Timestamps, Base):
    __tablename__ = "groups"
    __table_args__ = (
        org_fk("groups"),
        UniqueConstraint("org_id", "name", name="uq_groups_org_id"),
        UniqueConstraint("id", "org_id", name="uq_groups_id"),
        CheckConstraint("source IN ('manual','idp')", name="ck_groups_source"),
        PrimaryKeyConstraint(name="pk_groups"),
    )
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20), server_default="manual")
    external_id: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_groups_created_by_users")
    )
    org_id: Mapped[UUID] = mapped_column(nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT", name="fk_groups_tenant_id_tenants"),
        nullable=False,
    )


class GroupMember(Base):
    __tablename__ = "group_members"
    __table_args__ = (
        org_fk("group_members"),
        ForeignKeyConstraint(
            ["group_id", "org_id"],
            ["groups.id", "groups.org_id"],
            ondelete="CASCADE",
            name="fk_group_members_group_id_groups",
        ),
        ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["organization_members.org_id", "organization_members.user_id"],
            ondelete="CASCADE",
            name="fk_group_members_org_id_organization_members",
        ),
        Index("ix_group_members_user", "user_id", "org_id"),
        PrimaryKeyConstraint(name="pk_group_members"),
    )
    group_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    added_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_group_members_added_by_users")
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )
    org_id: Mapped[UUID] = mapped_column(nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT", name="fk_group_members_tenant_id_tenants"),
        nullable=False,
    )


class OrgCapabilityGrant(Identity, Base):
    __tablename__ = "org_capability_grants"
    __table_args__ = (
        org_fk("org_capability_grants"),
        UniqueConstraint(
            "org_id",
            "capability",
            "principal_type",
            "principal_id",
            name="uq_org_capability_grants_org_id",
        ),
        CheckConstraint(
            "capability IN "
            "('cap_create_agent','cap_create_skill','cap_create_tool','cap_register_mcp',"
            "'cap_connect_database','cap_share_org_wide','cap_publish_marketplace')",
            name="ck_org_capability_grants_capability",
        ),
        CheckConstraint(
            "principal_type IN ('user','group','organization')",
            name="ck_org_capability_grants_principal_type",
        ),
        CheckConstraint(
            "principal_type != 'organization' OR principal_id = org_id",
            name="ck_org_capability_grants_organization_principal",
        ),
        PrimaryKeyConstraint(name="pk_org_capability_grants"),
    )
    capability: Mapped[str] = mapped_column(String(40))
    principal_type: Mapped[str] = mapped_column(String(20))
    principal_id: Mapped[UUID] = mapped_column()
    granted_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "users.id", ondelete="SET NULL", name="fk_org_capability_grants_granted_by_users"
        )
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )
    org_id: Mapped[UUID] = mapped_column(nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "tenants.id", ondelete="RESTRICT", name="fk_org_capability_grants_tenant_id_tenants"
        ),
        nullable=False,
    )


class OrgInvitation(Identity, Base):
    __tablename__ = "org_invitations"
    __table_args__ = (
        org_fk("org_invitations"),
        CheckConstraint(
            "role IN ('member','builder','admin','auditor')", name="ck_org_invitations_role"
        ),
        CheckConstraint("expires_at > created_at", name="ck_org_invitations_expiration"),
        Index(
            "ix_org_invitations_pending",
            "org_id",
            "email",
            postgresql_where=text("accepted_at IS NULL AND revoked_at IS NULL"),
            sqlite_where=text("accepted_at IS NULL AND revoked_at IS NULL"),
        ),
        PrimaryKeyConstraint(name="pk_org_invitations"),
        UniqueConstraint("token_hash", name="uq_org_invitations_token_hash"),
    )
    email: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), server_default="member")
    group_ids: Mapped[list[str]] = mapped_column(JSON, server_default="[]")
    token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_org_invitations_accepted_by_users")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invited_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_org_invitations_invited_by_users")
    )
    org_id: Mapped[UUID] = mapped_column(nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT", name="fk_org_invitations_tenant_id_tenants"),
        nullable=False,
    )
