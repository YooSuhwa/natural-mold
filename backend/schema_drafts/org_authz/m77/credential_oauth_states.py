"""Frozen m77 credential_oauth_states fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "credential_oauth_states",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column(
            "state_hash", sa.String(length=64), nullable=False, primary_key=False, unique=True
        ),
        sa.Column("credential_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("redirect_uri", sa.String(length=500), nullable=False, primary_key=False),
        sa.Column("code_verifier", sa.Text(), nullable=True, primary_key=False),
        sa.Column("nonce", sa.String(length=128), nullable=True, primary_key=False),
        sa.Column(
            "origin", sa.String(length=40), nullable=False, primary_key=False, default="credential"
        ),
        sa.Column("return_to", sa.String(length=500), nullable=True, primary_key=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True, primary_key=False, default=dict),
        sa.Column("consumed_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False, primary_key=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["credential_id"], ["credentials.id"], name=None, ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=None),
    )
