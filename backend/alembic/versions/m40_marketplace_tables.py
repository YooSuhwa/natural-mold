"""M40: Marketplace core tables (ADR-017 Slice A).

Revision ID: m40_marketplace_tables
Revises: m39_dedupe_system_creds
Create Date: 2026-05-18

Creates the 5 marketplace tables described in Spec §3.2~§3.6:

* ``marketplace_items``        — catalog entry (1:N versions)
* ``marketplace_item_acl``     — restricted visibility recipient list
* ``marketplace_versions``     — immutable snapshots
* ``marketplace_installations``— per-user installed resource pointer
* ``marketplace_publication_links`` — my-resource → my-published-item back-ref

The ``marketplace_items.latest_version_id`` ↔ ``marketplace_versions.id``
relation is **circular**; this migration handles it in three steps:

1. ``CREATE TABLE marketplace_items`` without ``latest_version_id`` FK
2. ``CREATE TABLE marketplace_versions`` (FK to items already in place)
3. ``ALTER TABLE marketplace_items ADD COLUMN latest_version_id + FK``

Downgrade reverses the order: drop FK then drop tables in reverse
dependency order so PostgreSQL doesn't complain about referenced rows.

Reversible. SQLite test path is exercised by ``tests/conftest.py`` via
``Base.metadata.create_all`` — the migration is only used by Postgres
runs, so we keep the schema strictly portable (CHECK constraints with
explicit names, ``Uuid()`` instead of ``PG_UUID``).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m40_marketplace_tables"
down_revision = "m39_dedupe_system_creds"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return name in inspector.get_table_names()


def _has_constraint(table: str, name: str, kind: str = "check") -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    if kind == "foreignkey":
        return any(fk.get("name") == name for fk in inspector.get_foreign_keys(table))
    if kind == "check":
        try:
            return any(
                c.get("name") == name for c in inspector.get_check_constraints(table)
            )
        except NotImplementedError:
            return False
    return False


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return any(col["name"] == column for col in inspector.get_columns(table))


def _has_index(table: str, name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return any(ix.get("name") == name for ix in inspector.get_indexes(table))


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


LATEST_VERSION_FK = "fk_marketplace_items_latest_version"


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    # 1. marketplace_items (without latest_version_id FK)
    if not _has_table("marketplace_items"):
        op.create_table(
            "marketplace_items",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("resource_type", sa.String(20), nullable=False),
            sa.Column(
                "owner_user_id",
                sa.Uuid(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("is_listed", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("slug", sa.String(220), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("icon_url", sa.Text(), nullable=True),
            sa.Column(
                "visibility",
                sa.String(20),
                nullable=False,
                server_default="private",
            ),
            sa.Column(
                "status",
                sa.String(20),
                nullable=False,
                server_default="draft",
            ),
            sa.Column(
                "moderation_status",
                sa.String(20),
                nullable=False,
                server_default="approved",
            ),
            sa.Column("source_kind", sa.String(40), nullable=True),
            sa.Column("source_url", sa.Text(), nullable=True),
            sa.Column("source_external_id", sa.String(240), nullable=True),
            # latest_version_id added later via ALTER TABLE.
            sa.Column("tags", sa.JSON(), nullable=True),
            sa.Column("categories", sa.JSON(), nullable=True),
            sa.Column("locale", sa.String(20), nullable=True),
            sa.Column("metadata", sa.JSON(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint(
                "resource_type IN ('agent','mcp','skill')",
                name="ck_marketplace_resource_type",
            ),
            sa.CheckConstraint(
                "visibility IN ('private','restricted','public','unlisted','system')",
                name="ck_marketplace_visibility",
            ),
            sa.CheckConstraint(
                "status IN ('draft','published','deprecated','disabled')",
                name="ck_marketplace_status",
            ),
            sa.CheckConstraint(
                "(is_system = false) OR (owner_user_id IS NULL)",
                name="ck_marketplace_system_owner",
            ),
        )

    # Partial UNIQUE indexes (Postgres) — SQLite ignores ``postgresql_where``.
    if not _has_index("marketplace_items", "uq_marketplace_items_system_slug"):
        if _is_postgres():
            op.create_index(
                "uq_marketplace_items_system_slug",
                "marketplace_items",
                ["resource_type", "slug"],
                unique=True,
                postgresql_where=sa.text("is_system = true"),
            )
        else:
            # SQLite test path — partial unique index syntax is supported
            # since 3.8 but Alembic doesn't pass through ``sqlite_where``
            # reliably. The application-level uniqueness check is the
            # fallback; tests don't depend on this constraint.
            pass

    if _is_postgres() and not _has_index(
        "marketplace_items", "uq_marketplace_items_owner_slug"
    ):
        op.create_index(
            "uq_marketplace_items_owner_slug",
            "marketplace_items",
            ["owner_user_id", "resource_type", "slug"],
            unique=True,
            postgresql_where=sa.text("owner_user_id IS NOT NULL"),
        )

    if not _has_index("marketplace_items", "ix_marketplace_items_listed"):
        op.create_index(
            "ix_marketplace_items_listed",
            "marketplace_items",
            ["is_listed", "visibility", "status"],
        )

    # 2. marketplace_versions
    if not _has_table("marketplace_versions"):
        op.create_table(
            "marketplace_versions",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column(
                "item_id",
                sa.Uuid(),
                sa.ForeignKey("marketplace_items.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("version_label", sa.String(80), nullable=False),
            sa.Column("version_number", sa.Integer(), nullable=False),
            sa.Column("resource_type", sa.String(20), nullable=False),
            sa.Column("payload_kind", sa.String(40), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("storage_path", sa.String(500), nullable=True),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column(
                "size_bytes",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column("credential_requirements", sa.JSON(), nullable=True),
            sa.Column("dependency_requirements", sa.JSON(), nullable=True),
            sa.Column("execution_profile", sa.JSON(), nullable=True),
            sa.Column("release_notes", sa.Text(), nullable=True),
            sa.Column("source_commit", sa.String(80), nullable=True),
            sa.Column("source_ref", sa.String(120), nullable=True),
            sa.Column("source_path", sa.Text(), nullable=True),
            sa.Column(
                "created_by",
                sa.Uuid(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.CheckConstraint(
                "resource_type IN ('agent','mcp','skill')",
                name="ck_marketplace_version_resource_type",
            ),
            sa.CheckConstraint(
                "payload_kind IN ('skill_package','agent_spec','mcp_template')",
                name="ck_marketplace_payload_kind",
            ),
            sa.UniqueConstraint(
                "item_id",
                "version_number",
                name="uq_marketplace_versions_item_number",
            ),
        )

    if not _has_index("marketplace_versions", "ix_marketplace_versions_content_hash"):
        op.create_index(
            "ix_marketplace_versions_content_hash",
            "marketplace_versions",
            ["content_hash"],
        )

    # 3. Add latest_version_id + FK to marketplace_items now that versions exists.
    if not _has_column("marketplace_items", "latest_version_id"):
        op.add_column(
            "marketplace_items",
            sa.Column("latest_version_id", sa.Uuid(), nullable=True),
        )
    if _is_postgres() and not _has_constraint(
        "marketplace_items", LATEST_VERSION_FK, kind="foreignkey"
    ):
        op.create_foreign_key(
            LATEST_VERSION_FK,
            "marketplace_items",
            "marketplace_versions",
            ["latest_version_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # 4. marketplace_item_acl (composite PK)
    if not _has_table("marketplace_item_acl"):
        op.create_table(
            "marketplace_item_acl",
            sa.Column(
                "item_id",
                sa.Uuid(),
                sa.ForeignKey("marketplace_items.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column(
                "user_id",
                sa.Uuid(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column(
                "permission",
                sa.String(20),
                nullable=False,
                server_default="install",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.CheckConstraint(
                "permission IN ('view','install','manage')",
                name="ck_marketplace_acl_permission",
            ),
        )

    # 5. marketplace_installations
    if not _has_table("marketplace_installations"):
        op.create_table(
            "marketplace_installations",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Uuid(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "item_id",
                sa.Uuid(),
                sa.ForeignKey("marketplace_items.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "version_id",
                sa.Uuid(),
                # ON DELETE RESTRICT — installation protects version from
                # accidental deletion. Operators must uninstall first.
                sa.ForeignKey("marketplace_versions.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("resource_type", sa.String(20), nullable=False),
            sa.Column(
                "installed_agent_id",
                sa.Uuid(),
                sa.ForeignKey("agents.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column(
                "installed_mcp_server_id",
                sa.Uuid(),
                sa.ForeignKey("mcp_servers.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column(
                "installed_skill_id",
                sa.Uuid(),
                sa.ForeignKey("skills.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column(
                "install_status",
                sa.String(30),
                nullable=False,
                server_default="active",
            ),
            sa.Column(
                "is_dirty",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column(
                "installed_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.CheckConstraint(
                "(resource_type = 'agent' AND installed_agent_id IS NOT NULL "
                " AND installed_mcp_server_id IS NULL AND installed_skill_id IS NULL) "
                "OR (resource_type = 'mcp' AND installed_mcp_server_id IS NOT NULL "
                " AND installed_agent_id IS NULL AND installed_skill_id IS NULL) "
                "OR (resource_type = 'skill' AND installed_skill_id IS NOT NULL "
                " AND installed_agent_id IS NULL AND installed_mcp_server_id IS NULL)",
                name="ck_marketplace_install_resource_target",
            ),
            sa.CheckConstraint(
                "install_status IN ('active','needs_setup','disabled','uninstalled')",
                name="ck_marketplace_install_status",
            ),
        )

    if not _has_index("marketplace_installations", "ix_marketplace_install_user_item"):
        op.create_index(
            "ix_marketplace_install_user_item",
            "marketplace_installations",
            ["user_id", "item_id"],
        )
    if not _has_index(
        "marketplace_installations", "ix_marketplace_install_user_resource"
    ):
        op.create_index(
            "ix_marketplace_install_user_resource",
            "marketplace_installations",
            ["user_id", "resource_type"],
        )

    # 6. marketplace_publication_links
    if not _has_table("marketplace_publication_links"):
        op.create_table(
            "marketplace_publication_links",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Uuid(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "item_id",
                sa.Uuid(),
                sa.ForeignKey("marketplace_items.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("resource_type", sa.String(20), nullable=False),
            sa.Column(
                "source_agent_id",
                sa.Uuid(),
                sa.ForeignKey("agents.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column(
                "source_mcp_server_id",
                sa.Uuid(),
                sa.ForeignKey("mcp_servers.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column(
                "source_skill_id",
                sa.Uuid(),
                sa.ForeignKey("skills.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.CheckConstraint(
                "resource_type IN ('agent','mcp','skill')",
                name="ck_pub_link_resource_type",
            ),
            sa.CheckConstraint(
                "(resource_type = 'agent' AND source_agent_id IS NOT NULL "
                " AND source_mcp_server_id IS NULL AND source_skill_id IS NULL) "
                "OR (resource_type = 'mcp' AND source_mcp_server_id IS NOT NULL "
                " AND source_agent_id IS NULL AND source_skill_id IS NULL) "
                "OR (resource_type = 'skill' AND source_skill_id IS NOT NULL "
                " AND source_agent_id IS NULL AND source_mcp_server_id IS NULL)",
                name="ck_pub_link_target",
            ),
            sa.UniqueConstraint("item_id", name="uq_pub_link_item"),
        )

    if not _has_index("marketplace_publication_links", "ix_pub_link_resource"):
        op.create_index(
            "ix_pub_link_resource",
            "marketplace_publication_links",
            ["user_id", "resource_type"],
        )


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------


def downgrade() -> None:
    # Reverse order so PostgreSQL's referential checks are satisfied.
    if _has_index("marketplace_publication_links", "ix_pub_link_resource"):
        op.drop_index("ix_pub_link_resource", table_name="marketplace_publication_links")
    if _has_table("marketplace_publication_links"):
        op.drop_table("marketplace_publication_links")

    if _has_index("marketplace_installations", "ix_marketplace_install_user_resource"):
        op.drop_index(
            "ix_marketplace_install_user_resource",
            table_name="marketplace_installations",
        )
    if _has_index("marketplace_installations", "ix_marketplace_install_user_item"):
        op.drop_index(
            "ix_marketplace_install_user_item",
            table_name="marketplace_installations",
        )
    if _has_table("marketplace_installations"):
        op.drop_table("marketplace_installations")

    if _has_table("marketplace_item_acl"):
        op.drop_table("marketplace_item_acl")

    # Break circular FK before dropping versions/items.
    if _is_postgres() and _has_constraint(
        "marketplace_items", LATEST_VERSION_FK, kind="foreignkey"
    ):
        op.drop_constraint(
            LATEST_VERSION_FK, "marketplace_items", type_="foreignkey"
        )
    if _has_column("marketplace_items", "latest_version_id"):
        op.drop_column("marketplace_items", "latest_version_id")

    if _has_index(
        "marketplace_versions", "ix_marketplace_versions_content_hash"
    ):
        op.drop_index(
            "ix_marketplace_versions_content_hash",
            table_name="marketplace_versions",
        )
    if _has_table("marketplace_versions"):
        op.drop_table("marketplace_versions")

    if _has_index("marketplace_items", "ix_marketplace_items_listed"):
        op.drop_index("ix_marketplace_items_listed", table_name="marketplace_items")
    if _is_postgres() and _has_index(
        "marketplace_items", "uq_marketplace_items_owner_slug"
    ):
        op.drop_index(
            "uq_marketplace_items_owner_slug", table_name="marketplace_items"
        )
    if _is_postgres() and _has_index(
        "marketplace_items", "uq_marketplace_items_system_slug"
    ):
        op.drop_index(
            "uq_marketplace_items_system_slug", table_name="marketplace_items"
        )
    if _has_table("marketplace_items"):
        op.drop_table("marketplace_items")
