"""M55: user profile personalization.

Revision ID: m55_user_profile_personalization
Revises: m54_agent_identity
Create Date: 2026-06-04
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m55_user_profile_personalization"
down_revision = "m54_agent_identity"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.add_column("users", sa.Column("display_name", sa.String(length=80), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "avatar_mode",
            sa.String(length=20),
            nullable=False,
            server_default="auto",
        ),
    )
    op.add_column("users", sa.Column("avatar_initials", sa.String(length=4), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "avatar_color",
            sa.String(length=20),
            nullable=False,
            server_default="mint",
        ),
    )
    op.add_column("users", sa.Column("avatar_image_path", sa.String(length=500), nullable=True))
    op.add_column(
        "users",
        sa.Column("avatar_updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    if _is_postgres():
        op.create_check_constraint(
            "ck_users_avatar_mode",
            "users",
            "avatar_mode in ('auto', 'initials', 'image')",
        )
        op.create_check_constraint(
            "ck_users_avatar_color",
            "users",
            "avatar_color in ('mint', 'sky', 'violet', 'amber', 'rose', 'slate')",
        )
    else:
        with op.batch_alter_table("users") as batch_op:
            batch_op.create_check_constraint(
                "ck_users_avatar_mode",
                "avatar_mode in ('auto', 'initials', 'image')",
            )
            batch_op.create_check_constraint(
                "ck_users_avatar_color",
                "avatar_color in ('mint', 'sky', 'violet', 'amber', 'rose', 'slate')",
            )


def downgrade() -> None:
    if _is_postgres():
        op.drop_constraint("ck_users_avatar_color", "users", type_="check")
        op.drop_constraint("ck_users_avatar_mode", "users", type_="check")
        op.drop_column("users", "avatar_updated_at")
        op.drop_column("users", "avatar_image_path")
        op.drop_column("users", "avatar_color")
        op.drop_column("users", "avatar_initials")
        op.drop_column("users", "avatar_mode")
        op.drop_column("users", "display_name")
    else:
        with op.batch_alter_table("users") as batch_op:
            batch_op.drop_constraint("ck_users_avatar_color", type_="check")
            batch_op.drop_constraint("ck_users_avatar_mode", type_="check")
            batch_op.drop_column("avatar_updated_at")
            batch_op.drop_column("avatar_image_path")
            batch_op.drop_column("avatar_color")
            batch_op.drop_column("avatar_initials")
            batch_op.drop_column("avatar_mode")
            batch_op.drop_column("display_name")
