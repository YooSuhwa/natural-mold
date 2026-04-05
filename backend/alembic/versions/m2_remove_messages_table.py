"""M2: remove messages table, change token_usages FK

Revision ID: m2_remove_messages
Revises: dcb1dff2e64d
Create Date: 2026-04-06
"""

import sqlalchemy as sa

from alembic import op

revision = "m2_remove_messages"
down_revision = "dcb1dff2e64d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. token_usages에 conversation_id 컬럼 추가 (nullable)
    op.add_column("token_usages", sa.Column("conversation_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_token_usages_conversation_id",
        "token_usages",
        "conversations",
        ["conversation_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # 2. 기존 데이터 마이그레이션: message_id → conversation_id (JOIN으로 채움)
    op.execute(
        """
        UPDATE token_usages
        SET conversation_id = m.conversation_id
        FROM messages m
        WHERE token_usages.message_id = m.id
        """
    )

    # 3. message_id FK 제거 + 컬럼 삭제
    op.drop_constraint("token_usages_message_id_fkey", "token_usages", type_="foreignkey")
    op.drop_column("token_usages", "message_id")

    # 4. messages 테이블 DROP
    op.drop_table("messages")

    # 5. conversation_id를 NOT NULL로 변경
    op.alter_column("token_usages", "conversation_id", nullable=False)


def downgrade() -> None:
    # messages 테이블 재생성 (데이터는 복원 불가)
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tool_calls", sa.JSON(), nullable=True),
        sa.Column("tool_call_id", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
    )

    # token_usages: conversation_id → message_id 복원
    op.add_column("token_usages", sa.Column("message_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "token_usages_message_id_fkey",
        "token_usages",
        "messages",
        ["message_id"],
        ["id"],
    )
    op.drop_constraint("fk_token_usages_conversation_id", "token_usages", type_="foreignkey")
    op.drop_column("token_usages", "conversation_id")
