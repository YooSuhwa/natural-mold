"""M61: chat navigator keyset pagination indexes.

source 컬럼을 포함한 복합 인덱스를 추가하고, M53 conversations 인덱스 2개를
제거한다. 제거 대상을 쓰던 쿼리는 전부 source equality 조건을 포함하거나
leading agent_id 컬럼만으로 충분해 신규 인덱스가 대체한다 (ORDER BY DESC는
ASC 인덱스의 backward scan으로 커버되므로 방향은 무관).

source 조건 없이 leading agent_id에만 의존하는 쿼리는 agent_service.py의 집계
3곳(list_agents, list_agent_summaries, get_agent)이다. 신규
인덱스는 폭이 2→4컬럼으로 늘어 이 쿼리들의 스캔 I/O가 소폭 증가할 수 있으나,
agent_id prefix 매칭은 그대로 유효하다.
"""

from __future__ import annotations

from alembic import op

revision = "m61_chat_navigator_indexes"
down_revision = "m60_credential_oauth_states"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_conversations_agent_source_pinned_updated_id",
        "conversations",
        ["agent_id", "source", "is_pinned", "updated_at", "id"],
    )
    op.create_index(
        "ix_conversations_agent_source_pinned_created_id",
        "conversations",
        ["agent_id", "source", "is_pinned", "created_at", "id"],
    )
    op.create_index(
        "ix_conversations_agent_source_updated_id",
        "conversations",
        ["agent_id", "source", "updated_at", "id"],
    )
    op.create_index(
        "ix_conversations_agent_source_created_id",
        "conversations",
        ["agent_id", "source", "created_at", "id"],
    )
    op.create_index(
        "ix_conversations_source_updated_id_agent",
        "conversations",
        ["source", "updated_at", "id", "agent_id"],
    )
    op.create_index(
        "ix_conversations_source_created_id_agent",
        "conversations",
        ["source", "created_at", "id", "agent_id"],
    )
    op.create_index("ix_agents_user_id_id", "agents", ["user_id", "id"])
    # 이 인덱스를 쓰던 쿼리는 source equality를 포함하거나 leading agent_id로 충분하다
    # — 전부 신규 인덱스가 대체한다
    op.drop_index("ix_conversations_agent_pinned_updated_id", table_name="conversations")
    op.drop_index("ix_conversations_agent_updated", table_name="conversations")


def downgrade() -> None:
    op.create_index(
        "ix_conversations_agent_updated",
        "conversations",
        ["agent_id", "updated_at"],
    )
    op.create_index(
        "ix_conversations_agent_pinned_updated_id",
        "conversations",
        ["agent_id", "is_pinned", "updated_at", "id"],
    )
    op.drop_index("ix_agents_user_id_id", table_name="agents")
    op.drop_index("ix_conversations_source_created_id_agent", table_name="conversations")
    op.drop_index("ix_conversations_source_updated_id_agent", table_name="conversations")
    op.drop_index("ix_conversations_agent_source_created_id", table_name="conversations")
    op.drop_index("ix_conversations_agent_source_updated_id", table_name="conversations")
    op.drop_index(
        "ix_conversations_agent_source_pinned_created_id",
        table_name="conversations",
    )
    op.drop_index(
        "ix_conversations_agent_source_pinned_updated_id",
        table_name="conversations",
    )
