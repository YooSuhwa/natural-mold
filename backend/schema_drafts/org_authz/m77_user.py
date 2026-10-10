"""Frozen m77 user schema for reviewed draft migration fixtures."""

from datetime import UTC, datetime

import sqlalchemy as sa

metadata = sa.MetaData()
sa.Table(
    "users",
    metadata,
    sa.Column("id", sa.Uuid(), nullable=False),
    sa.Column("email", sa.String(length=255), nullable=False),
    sa.Column("name", sa.String(length=100), nullable=False),
    sa.Column("display_name", sa.String(length=80), nullable=True),
    sa.Column(
        "avatar_mode", sa.String(length=20), server_default=sa.text("'auto'"), nullable=False
    ),
    sa.Column("avatar_initials", sa.String(length=4), nullable=True),
    sa.Column(
        "avatar_color", sa.String(length=20), server_default=sa.text("'mint'"), nullable=False
    ),
    sa.Column("avatar_image_path", sa.String(length=500), nullable=True),
    sa.Column("avatar_updated_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column(
        "created_at",
        sa.DateTime(),
        nullable=False,
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
    ),
    sa.Column("hashed_password", sa.String(length=255), nullable=True),
    sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    sa.Column("is_super_user", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_login_ip", sa.String(length=45), nullable=True),
    sa.Column("failed_login_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
    sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
    sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("email_verify_token", sa.String(length=64), nullable=True),
    sa.Column("email_verify_expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("password_reset_token", sa.String(length=64), nullable=True),
    sa.Column("password_reset_expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint("id"),
    sa.UniqueConstraint("email"),
)
