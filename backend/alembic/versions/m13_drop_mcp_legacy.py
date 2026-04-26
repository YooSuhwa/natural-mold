"""M13: drop mcp_servers + tools.mcp_server_id (M6.1 옵션 D 후속)

Revision ID: m13_drop_mcp_legacy
Revises: m12_drop_legacy_columns
Create Date: 2026-04-24

M9에서 mcp_servers → connections로 데이터 이관, M6.1에서 PATCH /api/tools/{id}
(옵션 D)로 사용자가 connection_id를 직접 바인딩할 수 있게 됐다. 이 마이그레이션은
이행기 동안 보존했던 legacy 테이블/컬럼/FK를 영구 제거한다.

순서 (FK 의존성 역방향):
1) tools.mcp_server_id (자동 명명 FK = `tools_mcp_server_id_fkey`) → drop
2) tools.mcp_server_id 컬럼 → drop
3) mcp_servers.credential_id FK (`fk_mcp_servers_credential_id`) → drop
4) mcp_servers 테이블 → drop

## pre-check
- legacy_invariants.collect_legacy_checks 가 m13 invariant
  ("MCP tools with legacy mcp_server_id but no connection_id (dead after M6.1)") 추가.
- preflight 0이 아니면 abort. m9 백필 누락 row가 있으면 connection_id 백필 후 재시도.

## downgrade
구조만 복구. 데이터 영구 상실.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m13_drop_mcp_legacy"
down_revision = "m12_drop_legacy_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _assert_no_stale_legacy_rows()

    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    # 1) tools.mcp_server_id FK + 컬럼 drop
    if not is_sqlite:
        op.drop_constraint("tools_mcp_server_id_fkey", "tools", type_="foreignkey")
    op.drop_column("tools", "mcp_server_id")

    # 2) mcp_servers.credential_id FK drop (m6_add_credentials에서 add)
    if not is_sqlite:
        op.drop_constraint("fk_mcp_servers_credential_id", "mcp_servers", type_="foreignkey")

    # 3) mcp_servers 테이블 drop
    op.drop_table("mcp_servers")


def _assert_no_stale_legacy_rows() -> None:
    """tools.mcp_server_id IS NOT NULL AND connection_id IS NULL → abort.

    m9 mapping 누락 row가 남아 있으면 drop 후 chat runtime이 fail-closed.
    legacy_invariants가 m13 invariant도 포함하므로 호출만 위임한다.
    """
    from app.services.legacy_invariants import collect_legacy_checks

    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def column_exists(table: str, column: str) -> bool:
        try:
            return column in {c["name"] for c in inspector.get_columns(table)}
        except sa.exc.NoSuchTableError:
            return False

    checks = collect_legacy_checks(bind.dialect.name, column_exists)

    errors: list[str] = []
    for label, sql in checks:
        try:
            count = bind.execute(sa.text(sql)).scalar() or 0
        except Exception:  # noqa: BLE001
            # 테이블이 이미 사라진 sqlite 시나리오 등 — 다음 체크로 진행.
            continue
        if count:
            errors.append(f"  - {label}: {count} row(s)")
    if errors:
        raise RuntimeError(
            "M13 preflight failed — stale legacy rows detected. "
            "Migration aborted to prevent permanent data loss. "
            "Resolve the following before retrying:\n" + "\n".join(errors)
        )


def downgrade() -> None:
    # downgrade: structure only — DATA LOSS IS PERMANENT.
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("auth_type", sa.String(length=20), nullable=False),
        sa.Column("auth_config", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("credential_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_foreign_key(
        "fk_mcp_servers_credential_id",
        "mcp_servers",
        "credentials",
        ["credential_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "tools",
        sa.Column("mcp_server_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "tools_mcp_server_id_fkey",
        "tools",
        "mcp_servers",
        ["mcp_server_id"],
        ["id"],
    )
