"""M14: partial unique index — (user_id, connection_id, name) WHERE type='mcp'

Revision ID: m14_uniq_mcp_tool_per_conn
Revises: m13_drop_mcp_legacy
Create Date: 2026-04-25

`POST /api/connections/{id}/discover-tools`가 user_id × connection × name 기준
idempotency를 약속하지만, 동시 두 요청이 같은 name에 대해 existing snapshot을
read-after-snapshot으로 미스하면 중복 Tool row가 생성될 수 있다 (Codex
adversarial Finding). 앱 레벨 가드(IntegrityError catch + savepoint)는 이
partial unique index를 최종 안전망으로 사용한다.

스코프: type='mcp'인 행에만 적용 — PREBUILT/CUSTOM/BUILTIN은 connection_id가
NULL일 수 있고 name이 자유롭게 중복될 수 있다.

PostgreSQL/SQLite 둘 다 partial unique index를 지원 (SQLite 3.8+).

## Pre-check 정책 (운영자 안전망)
M6.1 이전엔 unique 가드가 없었기에 dev/stg 환경에 (user, connection, name)
중복 mcp tool row가 잔존할 수 있다. 이 마이그레이션은 **silent dedupe를 하지
않는다** — `agent_tools.tool_id`가 ON DELETE CASCADE라 임의 dedupe는 agent
바인딩까지 silently 손실시킨다. 운영자가 명시적으로 정리 후 재실행해야 한다.
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

    # Pre-check: 중복이 있으면 fail-fast. silent DELETE는 agent_tools.tool_id
    # ON DELETE CASCADE를 통해 agent 바인딩까지 함께 사라지게 하므로 위험.
    # 운영자가 manual repair 후 재실행하는 경로로 유도.
    dup_groups = bind.execute(
        sa.text(
            """
            SELECT user_id, connection_id, name, COUNT(*) AS cnt
            FROM tools
            WHERE type = 'mcp'
              AND user_id IS NOT NULL
              AND connection_id IS NOT NULL
            GROUP BY user_id, connection_id, name
            HAVING COUNT(*) > 1
            ORDER BY COUNT(*) DESC
            LIMIT 5
            """
        )
    ).fetchall()
    if dup_groups:
        sample = "\n".join(
            f"  - user={row[0]} conn={row[1]} name={row[2]!r}: {row[3]} rows"
            for row in dup_groups
        )
        raise RuntimeError(
            "M14 preflight failed — duplicate (user_id, connection_id, name) MCP "
            "tool rows detected. Resolve manually before retrying.\n"
            "agent_tools.tool_id is ON DELETE CASCADE, so silent dedupe would "
            "drop agent bindings to the deleted rows. Required steps:\n"
            "1) Pick the canonical Tool id per (user, connection, name) group.\n"
            "2) UPDATE agent_tools SET tool_id=<canonical> WHERE tool_id IN <duplicates>.\n"
            "3) DELETE FROM tools WHERE id IN <duplicates>.\n"
            "4) Re-run alembic upgrade.\n"
            f"Sample groups (top 5):\n{sample}"
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
        op.create_index(
            INDEX_NAME,
            "tools",
            ["user_id", "connection_id", "name"],
            unique=True,
        )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="tools")
