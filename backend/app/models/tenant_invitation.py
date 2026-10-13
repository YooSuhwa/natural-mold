"""Company-scoped, hash-only invitation persistence."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.organization_base import Identity

_INVITATION_DIGEST_CHECK = (
    "length(token_hash) = 64 AND "
    "replace(replace(replace(replace(replace(replace(replace(replace("
    "replace(replace(replace(replace(replace(replace(replace(replace("
    "token_hash,'0',''),'1',''),'2',''),'3',''),'4',''),'5',''),'6',''),'7',''),"
    "'8',''),'9',''),'a',''),'b',''),'c',''),'d',''),'e',''),'f','') = ''"
)


class TenantInvitation(Identity, Base):
    __tablename__ = "tenant_invitations"
    __table_args__ = (
        CheckConstraint(
            "email = lower(trim(email))", name="ck_tenant_invitations_email_normalized"
        ),
        CheckConstraint(
            "role IS NULL OR role IN ('admin','auditor')", name="ck_tenant_invitations_role"
        ),
        CheckConstraint(
            "status IN ('pending','accepted','revoked')", name="ck_tenant_invitations_status"
        ),
        CheckConstraint("expires_at > created_at", name="ck_tenant_invitations_expiration"),
        CheckConstraint(
            "accepted_by IS NULL OR accepted_at IS NOT NULL",
            name="ck_tenant_invitations_accepted_actor_timestamp",
        ),
        CheckConstraint(
            "revoked_by IS NULL OR revoked_at IS NOT NULL",
            name="ck_tenant_invitations_revoked_actor_timestamp",
        ),
        CheckConstraint(
            "(status = 'pending' AND accepted_at IS NULL AND revoked_at IS NULL) OR "
            "(status = 'accepted' AND accepted_at IS NOT NULL AND revoked_at IS NULL) OR "
            "(status = 'revoked' AND accepted_at IS NULL AND revoked_at IS NOT NULL)",
            name="ck_tenant_invitations_terminal_state",
        ),
        CheckConstraint(_INVITATION_DIGEST_CHECK, name="ck_tenant_invitations_token_hash"),
        Index(
            "ix_tenant_invitations_pending",
            "tenant_id",
            "email",
            "role",
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
        UniqueConstraint("token_hash", name="uq_tenant_invitations_token_hash"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE", name="fk_tenant_invitations_tenant_id_tenants")
    )
    email: Mapped[str] = mapped_column(String(255))
    role: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), server_default="pending")
    token_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_tenant_invitations_created_by_users")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_tenant_invitations_accepted_by_users")
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_tenant_invitations_revoked_by_users")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
