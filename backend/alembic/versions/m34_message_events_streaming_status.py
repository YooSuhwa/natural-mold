"""M34: ``message_events.status`` + ``updated_at`` — W3-out streaming lifecycle.

Revision ID: m34_message_events_streaming_status
Revises: m33_add_linked_message_ids
Create Date: 2026-05-03

W3-out M2 — partial flush 도입에 따른 turn 상태 추적.

Schema 변경
- ``status VARCHAR(20) NOT NULL DEFAULT 'completed'`` + CHECK 제약
  (``status IN ('streaming','completed','failed')``).
  CHECK는 alembic-friendly + SQLite/Postgres 양쪽에서 동일하게 동작 (PG ENUM은
  in-flight ALTER 비용이 비싸고 SQLite에는 없어서 회피).
- ``updated_at TIMESTAMP NOT NULL DEFAULT now()`` — partial flush 마다 갱신.
- ``idx_message_events_status (conversation_id, status)`` — in-flight turn
  조회(M3 GET resume) 최적화.

기존 row 안전성
- ``DEFAULT 'completed' NOT NULL`` 추가는 PG 11+ 메타데이터 변경만으로 끝남
  (테이블 rewrite 없음). 기존 m33 이전 row는 ``status='completed'``,
  ``updated_at=now()`` 로 채워져 W6 / 기존 trace 조회 코드와 호환.

Production note
- 큰 운영 테이블에 적용 시 인덱스는 ``CREATE INDEX CONCURRENTLY`` 로 바꿔서
  락을 피해야 한다. alembic transactional context는 CONCURRENTLY를 지원하지
  않으므로 여기서는 일반 CREATE INDEX. 운영 절차에서 별도 처리 권장.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m34_message_events_status"
down_revision = "m33_add_linked_message_ids"
branch_labels = None
depends_on = None


_STATUS_VALUES = ("streaming", "completed", "failed")
_CHECK_NAME = "ck_message_events_status"
_INDEX_NAME = "idx_message_events_status"


def _now_default() -> sa.TextClause:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return sa.text("now()")
    return sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    op.add_column(
        "message_events",
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'completed'"),
        ),
    )
    op.add_column(
        "message_events",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=False),
            nullable=False,
            server_default=_now_default(),
        ),
    )
    op.create_check_constraint(
        _CHECK_NAME,
        "message_events",
        sa.column("status").in_(_STATUS_VALUES),
    )

    # PG에서는 ``CREATE INDEX CONCURRENTLY`` 로 long-table 락 회피. alembic의
    # 기본 트랜잭션 안에서는 CONCURRENTLY 가 금지되므로 ``autocommit_block``
    # 으로 트랜잭션을 일시 종료한다. SQLite 는 CONCURRENTLY 미지원이라 일반
    # ``create_index`` 사용.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(
                sa.text(
                    f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_INDEX_NAME} "
                    "ON message_events (conversation_id, status)"
                )
            )
    else:
        op.create_index(
            _INDEX_NAME,
            "message_events",
            ["conversation_id", "status"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX_NAME}"))
    else:
        op.drop_index(_INDEX_NAME, table_name="message_events")
    op.drop_constraint(_CHECK_NAME, "message_events", type_="check")
    op.drop_column("message_events", "updated_at")
    op.drop_column("message_events", "status")
