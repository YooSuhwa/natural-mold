"""M12: drop legacy tool.auth_config / tool.credential_id / agent_tools.config

Revision ID: m12_drop_legacy_columns
Revises: m11_custom_connection
Create Date: 2026-04-21

SCOPE_REDUCED_2 (2026-04-21): M6는 세 컬럼만 drop.
- tools.auth_config
- tools.credential_id (+ FK fk_tools_credential_id)
- agent_tools.config

mcp_servers 테이블 + tools.mcp_server_id 는 M6.1로 이월 (옵션 D 선행 필요).

## pre-check (프로덕션 필수)
docs/design-docs/m6-cleanup-migration-spec.md §5.1 (A)(C) 쿼리를 확인한 뒤 적용.

추가 사전점검 — PREBUILT `provider_name IS NULL` 행 확인:
    SELECT count(*) FROM tools WHERE type = 'prebuilt' AND provider_name IS NULL;
0 기대. 0이 아니면 `_resolve_legacy_tool_auth` 제거 이후 해당 행은
env fallback으로 평가되어 시맨틱이 바뀔 수 있음(이전에는 inline auth_config
반환). m10 매핑 누락 가능성이 있으므로 migration 전에 provider_name 백필
or 해당 row 정리 필요.

## downgrade
구조만 복구, 데이터는 영구 상실. 프로덕션 롤백은 DB 스냅샷 복원을 사용.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m12_drop_legacy_columns"
down_revision = "m11_custom_connection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Preflight: stale legacy row가 남아 있으면 upgrade를 abort한다. drop은
    # 데이터 영구 상실이므로 docstring의 pre-check를 실행 가능한 assertion으로.
    _assert_no_stale_legacy_rows()

    # 1) FK drop (tools.credential_id → credentials.id)
    #    FK 이름은 m6_add_credentials.py에서 명시한 "fk_tools_credential_id".
    op.drop_constraint("fk_tools_credential_id", "tools", type_="foreignkey")

    # 2) tools legacy 컬럼 drop
    op.drop_column("tools", "credential_id")
    op.drop_column("tools", "auth_config")

    # 3) agent_tools.config drop
    op.drop_column("agent_tools", "config")


def _assert_no_stale_legacy_rows() -> None:
    """Abort upgrade if any row still depends on the columns we're about to drop.

    sqlite in-memory 테스트에서는 conftest가 최신 모델을 바로 생성하므로
    legacy 컬럼 자체가 없을 수 있다. 그 경우 체크를 건너뛴다.
    """
    from app.services.legacy_invariants import collect_legacy_checks

    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def column_exists(table: str, column: str) -> bool:
        return column in {c["name"] for c in inspector.get_columns(table)}

    checks = collect_legacy_checks(bind.dialect.name, column_exists)

    errors: list[str] = []
    for label, sql in checks:
        count = bind.execute(sa.text(sql)).scalar() or 0
        if count:
            errors.append(f"  - {label}: {count} row(s)")
    if errors:
        raise RuntimeError(
            "M12 preflight failed — stale legacy rows detected. "
            "Migration aborted to prevent permanent data loss. "
            "Resolve the following before retrying:\n" + "\n".join(errors)
        )


def downgrade() -> None:
    # downgrade: structure only — DATA LOSS IS PERMANENT.
    # tools.auth_config / tools.credential_id, agent_tools.config 의 원본
    # 데이터는 복구되지 않는다. 프로덕션 롤백이 필요하면 alembic downgrade
    # 대신 DB 스냅샷 복원을 사용하라.

    # 1) agent_tools.config 복원
    op.add_column(
        "agent_tools",
        sa.Column("config", sa.JSON(), nullable=True),
    )

    # 2) tools.auth_config 복원
    op.add_column(
        "tools",
        sa.Column("auth_config", sa.JSON(), nullable=True),
    )

    # 3) tools.credential_id + FK 복원
    op.add_column(
        "tools",
        sa.Column("credential_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_tools_credential_id",
        "tools",
        "credentials",
        ["credential_id"],
        ["id"],
        ondelete="SET NULL",
    )
