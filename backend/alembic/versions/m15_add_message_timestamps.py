"""M15: conversations.message_timestamps (JSON) — 메시지 idx별 영구 timestamp.

Revision ID: m15_add_message_timestamps
Revises: m14_uniq_mcp_tool_per_conn
Create Date: 2026-04-28

LangChain BaseMessage에는 timestamp 메타가 없어서, list_messages 응답이 매 호출마다
`base_ts + idx*1ms`로 메시지 시각을 새로 매겼다. 결과적으로 새 메시지를 보낼 때마다
옛 메시지의 시각도 함께 변하는 비정상 동작이 발생.

이 컬럼은 (idx → ISO timestamp) 매핑을 영구 저장해, 메시지가 처음 list에 노출될 때
부여된 timestamp를 그 후로 변경되지 않게 만든다.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m15_add_message_timestamps"
down_revision = "m14_uniq_mcp_tool_per_conn"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "message_timestamps",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("conversations", "message_timestamps")
