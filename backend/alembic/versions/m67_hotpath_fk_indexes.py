"""M67: FK indexes for hot-path filters (BE-P6).

다섯 FK/필터 컬럼이 인덱스 없이 seq scan을 유발했다:

- token_usages.agent_id — 사용량 카드의 SUM(...) WHERE agent_id (usage_service)
- token_usages.conversation_id — 대화 삭제 ON DELETE CASCADE가 전체 스캔
- message_attachments.conversation_id — GET /messages 폴링·/files 필터
- mcp_servers.user_id — MCP 서버 목록 WHERE user_id
- agent_triggers.agent_id — 트리거 목록/집계 (기존 복합 인덱스는 status 선두라 미커버)

token_usages는 LLM 턴마다 1행씩 늘어나는 최속 성장 테이블인데 비-PK 인덱스가
없어 시간이 갈수록 악화되던 지점. 모델에도 index=True를 함께 달아
metadata.create_all(테스트/신규 환경)과 정합을 유지한다.
"""

from __future__ import annotations

from alembic import op

revision = "m67_hotpath_fk_indexes"
down_revision = "m66_template_skill_slugs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_token_usages_agent_id", "token_usages", ["agent_id"])
    op.create_index("ix_token_usages_conversation_id", "token_usages", ["conversation_id"])
    op.create_index(
        "ix_message_attachments_conversation_id", "message_attachments", ["conversation_id"]
    )
    op.create_index("ix_mcp_servers_user_id", "mcp_servers", ["user_id"])
    op.create_index("ix_agent_triggers_agent_id", "agent_triggers", ["agent_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_triggers_agent_id", table_name="agent_triggers")
    op.drop_index("ix_mcp_servers_user_id", table_name="mcp_servers")
    op.drop_index("ix_message_attachments_conversation_id", table_name="message_attachments")
    op.drop_index("ix_token_usages_conversation_id", table_name="token_usages")
    op.drop_index("ix_token_usages_agent_id", table_name="token_usages")
