"""Memory recall (feature plan v2 순번 15, memory-policy-plan 5.4, 5.5 and 5.12, PR-B2).

``memory_records`` gains the Korean terms keyword recall matches (``search_tokens``) and the
analyzer that made them (Nori or the suffix fallback). Four company tables (RLS
``DIRECT_TENANT``): ``memory_embeddings`` (a memory's vector per model, removed with the
memory), ``memory_recall_runs`` and ``memory_recalls`` (what recall put in front of a run,
no text) and ``memory_profile_docs`` ("내 프로필" and per-agent "작업 메모"). The system LLM
settings gain an ``embedding`` role (the minimal embedding model kind, Q2).

Existing memories get their terms from the suffix fallback here; the index job moves them to
Nori where OpenSearch runs. Downgrade drops the tables, the columns and the role's rows.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.config import settings
from app.migrations.org_authz_v1.rls_policies import create_policy_sql, enable_sql, table_policy

revision = "i22_memory_recall"
down_revision = "i21_memory_policy"
branch_labels = None
depends_on = None

_TABLES = ("memory_embeddings", "memory_recall_runs", "memory_recalls", "memory_profile_docs")
_ROLES_BEFORE = "role IN ('text_primary', 'text_fallback', 'image')"
_ROLES_AFTER = "role IN ('text_primary', 'text_fallback', 'image', 'embedding')"


def _scope(table: str) -> list[sa.Column | sa.Constraint]:
    return [
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=f"fk_{table}_tenant", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "tenant_id"],
            ["organizations.id", "organizations.tenant_id"],
            name=f"fk_{table}_org_tenant",
            ondelete="RESTRICT",
        ),
    ]


def _fk(table: str, column: str, target: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{target}.id"], name=f"fk_{table}_{column}_{target}", ondelete=ondelete
    )


def _tokenize_existing() -> None:
    from app.text.korean_terms import FALLBACK_ANALYZER, fallback_terms

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, content FROM memory_records")).fetchall()
    for row in rows:
        bind.execute(
            sa.text(
                "UPDATE memory_records SET search_tokens = :tokens, "
                "search_tokens_analyzer = :analyzer WHERE id = :id"
            ),
            {
                "tokens": " ".join(fallback_terms(row.content or "")),
                "analyzer": FALLBACK_ANALYZER,
                "id": row.id,
            },
        )


def upgrade() -> None:
    with op.batch_alter_table("memory_records") as batch:
        batch.add_column(sa.Column("search_tokens", sa.Text(), nullable=True))
        batch.add_column(sa.Column("search_tokens_analyzer", sa.String(20), nullable=True))
    with op.batch_alter_table("system_llm_settings") as batch:
        batch.drop_constraint("ck_system_llm_settings_role", type_="check")
        batch.create_check_constraint("ck_system_llm_settings_role", _ROLES_AFTER)

    op.create_table(
        "memory_embeddings",
        sa.Column("id", sa.Uuid(), nullable=False),
        *_scope("memory_embeddings"),
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("vector", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        _fk("memory_embeddings", "record_id", "memory_records", "CASCADE"),
        _fk("memory_embeddings", "user_id", "users", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_memory_embeddings"),
    )
    op.create_index(
        "uq_memory_embeddings_record_model",
        "memory_embeddings",
        ["record_id", "model", "dimensions"],
        unique=True,
    )
    op.create_index(
        "ix_memory_embeddings_user_model", "memory_embeddings", ["user_id", "model", "dimensions"]
    )

    op.create_table(
        "memory_recall_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        *_scope("memory_recall_runs"),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("dropped", sa.Integer(), nullable=False),
        sa.Column("fallback", sa.String(40), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        _fk("memory_recall_runs", "user_id", "users", "CASCADE"),
        _fk("memory_recall_runs", "conversation_id", "conversations", "CASCADE"),
        _fk("memory_recall_runs", "agent_id", "agents", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_memory_recall_runs"),
    )
    op.create_index("uq_memory_recall_runs_run", "memory_recall_runs", ["run_id"], unique=True)
    op.create_index(
        "ix_memory_recall_runs_conversation",
        "memory_recall_runs",
        ["conversation_id", "created_at"],
    )

    op.create_table(
        "memory_recalls",
        sa.Column("id", sa.Uuid(), nullable=False),
        *_scope("memory_recalls"),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("path", sa.String(10), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        _fk("memory_recalls", "user_id", "users", "CASCADE"),
        _fk("memory_recalls", "record_id", "memory_records", "CASCADE"),
        sa.CheckConstraint("path IN ('profile', 'auto', 'tool')", name="ck_memory_recalls_path"),
        sa.PrimaryKeyConstraint("id", name="pk_memory_recalls"),
    )
    op.create_index("ix_memory_recalls_run", "memory_recalls", ["run_id"])
    op.create_index("ix_memory_recalls_record", "memory_recalls", ["record_id"])

    op.create_table(
        "memory_profile_docs",
        sa.Column("id", sa.Uuid(), nullable=False),
        *_scope("memory_profile_docs"),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("lines", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        _fk("memory_profile_docs", "user_id", "users", "CASCADE"),
        _fk("memory_profile_docs", "agent_id", "agents", "CASCADE"),
        sa.CheckConstraint(
            "kind IN ('user_profile', 'work_memo')", name="ck_memory_profile_docs_kind"
        ),
        sa.CheckConstraint(
            "(kind = 'user_profile' AND agent_id IS NULL) "
            "OR (kind = 'work_memo' AND agent_id IS NOT NULL)",
            name="ck_memory_profile_docs_agent",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_memory_profile_docs"),
    )
    op.create_index(
        "uq_memory_profile_docs_user",
        "memory_profile_docs",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'user_profile'"),
        sqlite_where=sa.text("kind = 'user_profile'"),
    )
    op.create_index(
        "uq_memory_profile_docs_work",
        "memory_profile_docs",
        ["user_id", "agent_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'work_memo'"),
        sqlite_where=sa.text("kind = 'work_memo'"),
    )

    _tokenize_existing()

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    quote = bind.dialect.identifier_preparer.quote
    for table in _TABLES:
        for statement in create_policy_sql(quote, table_policy(quote, table)):
            op.execute(sa.text(statement))
    if settings.db_rls_enabled:
        for statement in enable_sql(quote, list(_TABLES)):
            op.execute(sa.text(statement))


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_table(table)
    op.execute(sa.text("DELETE FROM system_llm_settings WHERE role = 'embedding'"))
    with op.batch_alter_table("system_llm_settings") as batch:
        batch.drop_constraint("ck_system_llm_settings_role", type_="check")
        batch.create_check_constraint("ck_system_llm_settings_role", _ROLES_BEFORE)
    with op.batch_alter_table("memory_records") as batch:
        batch.drop_column("search_tokens_analyzer")
        batch.drop_column("search_tokens")
