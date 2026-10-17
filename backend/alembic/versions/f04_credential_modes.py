"""F-04 (plan m87): credential modes, per-user connections, credential allowed hosts.

* ``tools`` and ``mcp_servers`` get ``credential_mode`` (none/shared/per_user; D15) and
  ``credential_definition_key``. Existing rows keep today's behaviour: ``shared`` when
  they carry a credential (its definition key copied), else ``none`` (also the server
  default, so a raw insert never yields ``shared`` without a credential).
* ``credentials.allowed_hosts`` (D18; enforced later by F-11): filled from the hosts of
  the MCP servers using the credential. Credential definitions declare no hosts yet, so
  every other credential stays NULL (the plan's "cannot fill" case, Q14).
* ``resource_credential_bindings``: one caller's connection to a ``per_user`` resource.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import sqlalchemy as sa

from alembic import op

revision = "f04_credential_modes"
down_revision = "d02_agent_user_preferences"
branch_labels = None
depends_on = None

_TABLES = ("tools", "mcp_servers")
_MODES = "credential_mode IN ('none','shared','per_user')"
_BINDINGS = "resource_credential_bindings"


def _add_modes(table: str) -> None:
    with op.batch_alter_table(table) as batch:
        batch.add_column(
            sa.Column(
                "credential_mode", sa.String(length=20), server_default="none", nullable=False
            )
        )
        batch.add_column(sa.Column("credential_definition_key", sa.String(length=80)))
        batch.create_check_constraint(f"ck_{table}_credential_mode", _MODES)
    op.execute(f"UPDATE {table} SET credential_mode = 'shared' WHERE credential_id IS NOT NULL")
    op.execute(
        f"UPDATE {table} SET credential_definition_key = (SELECT c.definition_key "
        f"FROM credentials c WHERE c.id = {table}.credential_id) "
        "WHERE credential_id IS NOT NULL"
    )


def _fill_allowed_hosts() -> None:
    bind = op.get_bind()
    # Ids stay untyped: values round-trip as the driver returned them, on any dialect.
    servers = sa.table("mcp_servers", sa.column("credential_id"), sa.column("url", sa.String()))
    credentials = sa.table("credentials", sa.column("id"), sa.column("allowed_hosts", sa.JSON()))
    hosts: dict[object, set[str]] = {}
    query = sa.select(servers.c.credential_id, servers.c.url).where(
        servers.c.credential_id.is_not(None), servers.c.url.is_not(None)
    )
    for credential_id, url in bind.execute(query):
        host = urlsplit(url).hostname
        if host:
            hosts.setdefault(credential_id, set()).add(host.lower())
    for credential_id, found in hosts.items():
        bind.execute(
            credentials.update()
            .where(credentials.c.id == credential_id)
            .values(allowed_hosts=sorted(found))
        )


def _create_bindings() -> None:
    op.create_table(
        _BINDINGS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("resource_type", sa.String(length=20), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("credential_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["org_id", "tenant_id"],
            ["organizations.id", "organizations.tenant_id"],
            name=f"fk_{_BINDINGS}_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=f"fk_{_BINDINGS}_user_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["credentials.id"],
            name=f"fk_{_BINDINGS}_credential_id",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "resource_type IN ('tool','mcp_server')", name=f"ck_{_BINDINGS}_resource_type"
        ),
        sa.UniqueConstraint(
            "org_id",
            "user_id",
            "resource_type",
            "resource_id",
            name=f"uq_{_BINDINGS}_user_resource",
        ),
        sa.PrimaryKeyConstraint("id", name=f"pk_{_BINDINGS}"),
    )
    for column in ("user_id", "resource_id", "credential_id"):
        op.create_index(f"ix_{_BINDINGS}_{column}", _BINDINGS, [column])


def upgrade() -> None:
    for table in _TABLES:
        _add_modes(table)
    with op.batch_alter_table("credentials") as batch:
        batch.add_column(sa.Column("allowed_hosts", sa.JSON(), nullable=True))
    _fill_allowed_hosts()
    _create_bindings()


def downgrade() -> None:
    op.drop_table(_BINDINGS)
    with op.batch_alter_table("credentials") as batch:
        batch.drop_column("allowed_hosts")
    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"ck_{table}_credential_mode", type_="check")
            batch.drop_column("credential_definition_key")
            batch.drop_column("credential_mode")
