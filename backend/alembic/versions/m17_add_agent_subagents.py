"""M17: agent_subagents — 에이전트 자기참조 join 테이블 (서브에이전트 위임).

Revision ID: m17_add_agent_subagents
Revises: m16_add_opener_questions
Create Date: 2026-04-29

에이전트가 다른 에이전트를 "서브에이전트"로 호출할 수 있게 하는 자기참조
many-to-many 관계 테이블. parent_agent_id / sub_agent_id 모두 agents.id를 참조.

- PK: (parent_agent_id, sub_agent_id) 복합키 (중복 link 방지)
- INDEX: parent_agent_id 단독 (parent로부터 sub 조회 빈번)
- CHECK 제약: parent_agent_id != sub_agent_id (자기 자신 reject; service 레이어와 이중 가드)
- ON DELETE CASCADE: agent 삭제 시 link 자동 정리
- position: ordering (UI에서 정렬)
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m17_add_agent_subagents"
down_revision = "m16_add_opener_questions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_subagents",
        sa.Column(
            "parent_agent_id",
            sa.UUID(),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "sub_agent_id",
            sa.UUID(),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "position",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "parent_agent_id != sub_agent_id",
            name="ck_agent_subagents_no_self",
        ),
    )
    op.create_index(
        "ix_agent_subagents_parent_agent_id",
        "agent_subagents",
        ["parent_agent_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_subagents_parent_agent_id", table_name="agent_subagents")
    op.drop_table("agent_subagents")
