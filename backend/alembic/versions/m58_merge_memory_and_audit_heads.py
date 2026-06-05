"""M58: merge memory and audit migration heads.

Revision ID: m58_merge_memory_and_audit_heads
Revises: m56_memory_policy, m57_audit_events
Create Date: 2026-06-05
"""

from __future__ import annotations

revision = "m58_merge_memory_and_audit_heads"
down_revision = ("m56_memory_policy", "m57_audit_events")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
