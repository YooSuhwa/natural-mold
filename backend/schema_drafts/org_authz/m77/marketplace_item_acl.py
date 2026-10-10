"""Frozen m77 marketplace_item_acl fixture definition; do not derive from live ORM."""

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "marketplace_item_acl",
        metadata,
        sa.Column("item_id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column(
            "permission", sa.String(length=20), nullable=False, primary_key=False, default="install"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.CheckConstraint(
            "permission IN ('view','install','manage')", name="ck_marketplace_acl_permission"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["item_id"], ["marketplace_items.id"], name=None, ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("item_id", "user_id", name=None),
    )
