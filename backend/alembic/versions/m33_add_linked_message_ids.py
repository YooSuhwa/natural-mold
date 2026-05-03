"""M33: ``message_events.linked_message_ids`` — turn당 langchain msg id 저장.

Revision ID: m33_add_linked_message_ids
Revises: m32_add_message_events
Create Date: 2026-05-03

W6 정확도 개선 — turn별 trace에 그 turn에서 생성된 assistant 메시지의
parsed UUID(``MessageResponse.id``와 동일 형식)를 함께 저장. 공유 페이지의
chip 매핑이 turn 순서에 의존하던 것을 직접 매칭으로 교체할 수 있게 한다.

기존 row는 NULL이며 frontend는 NULL/빈 배열일 때 turn 순서 기반 폴백.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m33_add_linked_message_ids"
down_revision = "m32_add_message_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "message_events",
        sa.Column("linked_message_ids", sa.JSON, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("message_events", "linked_message_ids")
