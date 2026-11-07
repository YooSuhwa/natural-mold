"""Trigger memos (feature plan v2 순번 15, memory-policy-plan 5.12 and Q13, PR-B3b).

``memory_profile_docs`` gains ``kind = 'trigger_memo'``: one memo per schedule
(``trigger_id``, removed with the schedule), read only by that schedule's runs, with three
fixed sections (최근 상태와 다음 실행, 기본 정보, 실전 요령) carried per line. Downgrade
deletes trigger memos and drops the column.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "i25_memory_trigger_memo"
down_revision = "i24_memory_consolidation"
branch_labels = None
depends_on = None

_KIND_BEFORE = "kind IN ('user_profile', 'work_memo')"
_KIND_AFTER = "kind IN ('user_profile', 'work_memo', 'trigger_memo')"
_SHAPE_BEFORE = (
    "(kind = 'user_profile' AND agent_id IS NULL) OR (kind = 'work_memo' AND agent_id IS NOT NULL)"
)
_SHAPE_AFTER = (
    "(kind = 'user_profile' AND agent_id IS NULL AND trigger_id IS NULL) "
    "OR (kind = 'work_memo' AND agent_id IS NOT NULL AND trigger_id IS NULL) "
    "OR (kind = 'trigger_memo' AND agent_id IS NOT NULL AND trigger_id IS NOT NULL)"
)


def upgrade() -> None:
    with op.batch_alter_table("memory_profile_docs") as batch:
        batch.add_column(sa.Column("trigger_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_memory_profile_docs_trigger_id_agent_triggers",
            "agent_triggers",
            ["trigger_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.drop_constraint("ck_memory_profile_docs_kind", type_="check")
        batch.create_check_constraint("ck_memory_profile_docs_kind", _KIND_AFTER)
        batch.drop_constraint("ck_memory_profile_docs_agent", type_="check")
        batch.create_check_constraint("ck_memory_profile_docs_agent", _SHAPE_AFTER)
    op.create_index(
        "uq_memory_profile_docs_trigger",
        "memory_profile_docs",
        ["trigger_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'trigger_memo'"),
        sqlite_where=sa.text("kind = 'trigger_memo'"),
    )


def downgrade() -> None:
    op.drop_index("uq_memory_profile_docs_trigger", table_name="memory_profile_docs")
    op.execute(sa.text("DELETE FROM memory_profile_docs WHERE kind = 'trigger_memo'"))
    with op.batch_alter_table("memory_profile_docs") as batch:
        batch.drop_constraint("ck_memory_profile_docs_agent", type_="check")
        batch.create_check_constraint("ck_memory_profile_docs_agent", _SHAPE_BEFORE)
        batch.drop_constraint("ck_memory_profile_docs_kind", type_="check")
        batch.create_check_constraint("ck_memory_profile_docs_kind", _KIND_BEFORE)
        batch.drop_constraint(
            "fk_memory_profile_docs_trigger_id_agent_triggers", type_="foreignkey"
        )
        batch.drop_column("trigger_id")
