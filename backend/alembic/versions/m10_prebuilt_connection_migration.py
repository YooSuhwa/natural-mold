"""M10: tools.provider_name (스키마 + 백필 전용)

Revision ID: m10_prebuilt_connection
Revises: m9_migrate_mcp_to_connections
Create Date: 2026-04-18

ADR-008 §4 이행 — PREBUILT 도구를 per-user connection 경유로 해석하기 위한
스키마 변경 + 기존 PREBUILT tool의 provider_name 백필만 담당.

## upgrade
1. `tools.provider_name` VARCHAR(50) nullable 컬럼 추가 (SQLite는 batch_alter_table)
2. 기존 PREBUILT tools name 패턴 백필
   - `Naver *` → `naver`
   - `Google Search`, `Google Image Search`, `Google News Search` → `google_search`
   - `Gmail *`, `Calendar *` → `google_workspace`
   - `Google Chat *` → `google_chat`
   - 그 외 `type='prebuilt'` row는 WARN 로그 + NULL 유지 (수동 복구 대상)

## env → credential → default connection 시드는 여기서 하지 않는다
mock user는 app.main lifespan 시점에 생성되므로 migration 실행 시점엔 아직 없어
seed가 항상 silent skip되고 Alembic이 revision을 applied로 마킹해 재시도 경로가
사라지는 split-brain이 발생한다 (Codex adversarial P1). 시드는
`app.seed.prebuilt_connections.seed_mock_user_prebuilt_connections`가 lifespan seed
블록에서 mock user 생성 **직후** 매 기동마다 idempotent 실행한다.

## downgrade
- display_name/name에 `M10_SEED_MARKER`가 들어간 connection/credential 역삭제
  (lifespan seed가 이 프리픽스를 사용하므로 스키마 롤백 시 같이 정리된다).
- `tools.provider_name` 컬럼 drop (batch_alter_table, SQLite 호환).
- PREBUILT tool의 백필은 되돌리지 않음 (컬럼이 사라지므로 자연 소멸).
"""

from __future__ import annotations

import logging

import sqlalchemy as sa

from alembic import op

revision = "m10_prebuilt_connection"
down_revision = "m9_migrate_mcp_to_connections"
branch_labels = None
depends_on = None


logger = logging.getLogger("alembic.m10")

# downgrade에서 lifespan seed가 만든 credential/connection 행을 식별할 때 사용.
# 사용자가 UI로 만든 행은 마커가 없으므로 보존된다.
M10_SEED_MARKER = "[m10-auto-seed]"


def _backfill_provider_name(bind) -> None:
    """기존 PREBUILT tools의 name 패턴 → provider_name 백필."""
    mapping = [
        ("naver", "name LIKE 'Naver %'"),
        (
            "google_search",
            "name IN ('Google Search', 'Google Image Search', 'Google News Search')",
        ),
        (
            "google_workspace",
            "(name LIKE 'Gmail %' OR name LIKE 'Calendar %')",
        ),
        ("google_chat", "name LIKE 'Google Chat %'"),
    ]
    for provider, where in mapping:
        bind.execute(
            sa.text(
                f"UPDATE tools SET provider_name = :p "
                f"WHERE type = 'prebuilt' AND provider_name IS NULL AND {where}"
            ),
            {"p": provider},
        )

    # 매핑 실패 row 경고 — NULL로 남으면 런타임 legacy fallback 경로가 동작 (tolerance).
    leftovers = bind.execute(
        sa.text("SELECT id, name FROM tools WHERE type = 'prebuilt' AND provider_name IS NULL")
    ).fetchall()
    for row in leftovers:
        logger.warning(
            "m10: PREBUILT tool id=%s name=%r has no provider_name mapping — "
            "staying on legacy credential_id/env fallback path. "
            "Update app.seed.default_tools with provider_name and rerun m10.",
            row[0],
            row[1],
        )


def upgrade() -> None:
    # 1) tools.provider_name 컬럼 추가 — SQLite 호환을 위해 batch_alter_table
    with op.batch_alter_table("tools") as batch_op:
        batch_op.add_column(sa.Column("provider_name", sa.String(length=50), nullable=True))

    bind = op.get_bind()

    # 2) 기존 PREBUILT tools 백필 — name 패턴 → provider_name
    _backfill_provider_name(bind)

    # env → credential → connection 시드는 app.seed.prebuilt_connections가 lifespan에서
    # 수행한다 (mock user가 lifespan에서 생성되므로 migration 시점엔 존재하지 않음).


def downgrade() -> None:
    bind = op.get_bind()

    # m10이 만든 connection만 역삭제 — display_name에 마커가 들어간 행만 선별.
    # 사용자가 UI에서 만든 connection/credential은 마커가 없으므로 보존.
    marker_like = f"{M10_SEED_MARKER}%"
    bind.execute(
        sa.text("DELETE FROM connections WHERE type = 'prebuilt' AND display_name LIKE :m"),
        {"m": marker_like},
    )
    bind.execute(
        sa.text("DELETE FROM credentials WHERE name LIKE :m"),
        {"m": marker_like},
    )

    # provider_name 컬럼 drop — SQLite 호환 위해 batch_alter_table
    with op.batch_alter_table("tools") as batch_op:
        batch_op.drop_column("provider_name")
