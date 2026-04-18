"""M8: add connections table (parallel run, no existing code touched)

Revision ID: m8_add_connections
Revises: m7_add_credential_field_keys
Create Date: 2026-04-18
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m8_add_connections"
down_revision = "m7_add_credential_field_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("provider_name", sa.String(length=50), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("credential_id", sa.Uuid(), nullable=True),
        sa.Column("extra_config", sa.JSON(), nullable=True),
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="active",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["credentials.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_connections_user_type_provider",
        "connections",
        ["user_id", "type", "provider_name"],
    )
    # Partial unique index — "scope 당 default 1개 이하" 불변식을 DB 레벨에서 강제.
    # 앱 레벨 count+clear+insert 패턴은 동시 요청에서 race 가능 (Codex adversarial
    # P2: 두 요청이 동시에 default=true로 insert → 둘 다 default 남음).
    # SQLite 3.8+ / PostgreSQL 모두 partial unique index 지원.
    op.create_index(
        "uq_connections_one_default_per_scope",
        "connections",
        ["user_id", "type", "provider_name"],
        unique=True,
        postgresql_where=sa.text("is_default = true"),
        sqlite_where=sa.text("is_default = 1"),
    )


def downgrade() -> None:
    # IF EXISTS: 이전 버전의 m8이 partial unique index 없이 돌았던 DB에서도
    # 안전하게 downgrade할 수 있도록 (신규 추가 인덱스만 방어). 본 테이블/인덱스는
    # 처음부터 쌍으로 관리되므로 기본 drop.
    op.execute("DROP INDEX IF EXISTS uq_connections_one_default_per_scope")
    op.drop_index("ix_connections_user_type_provider", table_name="connections")
    op.drop_table("connections")
