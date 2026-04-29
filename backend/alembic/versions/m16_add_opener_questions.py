"""M16: agents.opener_questions (JSON nullable) — 새 채팅 빈 화면 예시 질문 리스트.

Revision ID: m16_add_opener_questions
Revises: m15_add_message_timestamps
Create Date: 2026-04-28

각 에이전트가 새 대화 시작 시 사용자에게 보여줄 예시 질문(opener)을 최대 12개
저장한다. 클릭 시 composer에 텍스트 주입(전송 X). validator는 schemas 레이어에서
처리(≤12개, 항목 1~200자).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m16_add_opener_questions"
down_revision = "m15_add_message_timestamps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("opener_questions", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agents", "opener_questions")
