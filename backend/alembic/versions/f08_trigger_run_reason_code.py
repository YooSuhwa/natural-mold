"""F-08 (plan 6.x ``agent_trigger_runs.reason_code``, 11.6): typed BLOCKED reason.

A trigger run refused at dispatch keeps ``status='failed'`` (or ``skipped`` while
authorization is unavailable) so the consecutive-failure auto pause still applies;
the new nullable column carries the 11.6 reason (``permission_revoked``,
``owner_left``, ``delegation_broken``, ...). Existing rows stay NULL.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "f08_trigger_run_reason_code"
down_revision = "f14_run_attribution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_trigger_runs", sa.Column("reason_code", sa.String(40), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_trigger_runs", "reason_code")
