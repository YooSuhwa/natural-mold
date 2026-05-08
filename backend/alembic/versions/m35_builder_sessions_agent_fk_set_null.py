"""builder_sessions.agent_id FK ON DELETE SET NULL

기존 FK 는 ondelete 정책 없음 → agent 삭제 시 ForeignKeyViolationError.
세션은 빌드 흐름 감사 트레일이라 agent 와 함께 cascade delete 하지 않고
reference 만 끊는다.

Revision ID: m35_builder_session_fk_setnull
Revises: m34_message_events_status
Create Date: 2026-05-08
"""

from alembic import op

revision = "m35_builder_session_fk_setnull"
down_revision = "m34_message_events_status"
branch_labels = None
depends_on = None


CONSTRAINT_NAME = "builder_sessions_agent_id_fkey"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT_NAME, "builder_sessions", type_="foreignkey")
    op.create_foreign_key(
        CONSTRAINT_NAME,
        "builder_sessions",
        "agents",
        ["agent_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT_NAME, "builder_sessions", type_="foreignkey")
    op.create_foreign_key(
        CONSTRAINT_NAME,
        "builder_sessions",
        "agents",
        ["agent_id"],
        ["id"],
    )
