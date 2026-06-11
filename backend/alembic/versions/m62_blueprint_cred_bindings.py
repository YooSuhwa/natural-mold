"""M62: persist Agent Blueprint credential bindings.

Revision ID: m62_blueprint_cred_bindings
Revises: m61_agent_blueprints
Create Date: 2026-06-11

The revision id is intentionally short — Alembic stores it in
``alembic_version.version_num VARCHAR(32)``, so anything over 32 chars
fails ``upgrade head`` on every environment.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m62_blueprint_cred_bindings"
down_revision = "m61_agent_blueprints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_blueprints",
        sa.Column("credential_bindings", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_blueprints", "credential_bindings")
