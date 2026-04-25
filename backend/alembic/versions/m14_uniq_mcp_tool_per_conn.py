"""M14: partial unique index — (user_id, connection_id, name) WHERE type='mcp'

Revision ID: m14_uniq_mcp_tool_per_conn
Revises: m13_drop_mcp_legacy
Create Date: 2026-04-25

`POST /api/connections/{id}/discover-tools`가 user_id × connection × name 기준
idempotency를 약속하지만, 동시 두 요청이 같은 name에 대해 existing snapshot을
read-after-snapshot으로 미스하면 중복 Tool row가 생성될 수 있다 (Codex
adversarial Finding). 앱 레벨 가드(IntegrityError catch)는 이 partial unique
index를 최종 안전망으로 사용한다.

스코프: type='mcp'인 행에만 적용 — PREBUILT/CUSTOM/BUILTIN은 connection_id가
NULL일 수 있고 name이 자유롭게 중복될 수 있다.

PostgreSQL/SQLite 둘 다 partial unique index를 지원 (SQLite 3.8+).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m14_uniq_mcp_tool_per_conn"
down_revision = "m13_drop_mcp_legacy"
branch_labels = None
depends_on = None


INDEX_NAME = "uq_mcp_tools_user_connection_name"


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # 사전 정리: 기존 중복 row가 남아있으면 인덱스 생성이 실패한다. 동일 (user,
    # connection, name) 그룹에서 가장 오래된 row만 유지하고 나머지는 삭제 (created_at
    # ASC, id를 tie-breaker). M6.1 이전엔 unique 가드가 없었으므로 dev/stg에 잔존
    # 가능성 있음. PoC 단계라 데이터 보존 손실은 비치명적.
    if dialect == "postgresql":
        bind.execute(
            sa.text(
                """
                DELETE FROM tools
                WHERE id IN (
                    SELECT id FROM (
                        SELECT id, ROW_NUMBER() OVER (
                            PARTITION BY user_id, connection_id, name
                            ORDER BY created_at ASC, id ASC
                        ) AS rn
                        FROM tools
                        WHERE type = 'mcp'
                          AND user_id IS NOT NULL
                          AND connection_id IS NOT NULL
                    ) ranked
                    WHERE rn > 1
                )
                """
            )
        )
    elif dialect == "sqlite":
        # SQLite도 ROW_NUMBER 지원 (3.25+) — round-trip 안전성 위해 동일 SQL.
        bind.execute(
            sa.text(
                """
                DELETE FROM tools
                WHERE id IN (
                    SELECT id FROM (
                        SELECT id, ROW_NUMBER() OVER (
                            PARTITION BY user_id, connection_id, name
                            ORDER BY created_at ASC, id ASC
                        ) AS rn
                        FROM tools
                        WHERE type = 'mcp'
                          AND user_id IS NOT NULL
                          AND connection_id IS NOT NULL
                    ) ranked
                    WHERE rn > 1
                )
                """
            )
        )

    # partial unique index — type='mcp'인 행에만 적용
    if dialect == "postgresql":
        op.create_index(
            INDEX_NAME,
            "tools",
            ["user_id", "connection_id", "name"],
            unique=True,
            postgresql_where=sa.text("type = 'mcp'"),
        )
    elif dialect == "sqlite":
        # SQLAlchemy 2.x: sqlite_where 인자 미지원. raw DDL.
        op.execute(
            sa.text(
                f"CREATE UNIQUE INDEX {INDEX_NAME} "
                "ON tools (user_id, connection_id, name) "
                "WHERE type = 'mcp'"
            )
        )
    else:
        # 알 수 없는 dialect — 안전하게 일반 unique index (전체 행 적용)
        # 스코프 외이므로 실 호출 가능성 낮음.
        op.create_index(
            INDEX_NAME,
            "tools",
            ["user_id", "connection_id", "name"],
            unique=True,
        )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="tools")
