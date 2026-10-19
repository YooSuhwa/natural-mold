"""H-08 (plan 10.10) SSO hardening on the B-15 tables.

* ``tenant_domains``: a domain is unique only once *verified* (partial unique index);
  several companies may hold a pending claim, one claim per company and domain.
* ``sso_connections.trust_email_claim``: opt-in to treat an IdP/directory email without
  ``email_verified`` as verified when attaching to an existing account (default false).
* ``refresh_tokens.auth_method``: how the session signed in (``password`` /
  ``sso:<tenant id>``; NULL = password-origin) so SSO-required companies can refuse
  password-origin refreshes.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "h08_sso_hardening"
down_revision = "h02_notifications"
branch_labels = None
depends_on = None

_VERIFIED = "status = 'verified'"


def upgrade() -> None:
    with op.batch_alter_table("tenant_domains") as batch:
        batch.drop_constraint("uq_tenant_domains_domain", type_="unique")
        batch.create_unique_constraint("uq_tenant_domains_tenant_id", ["tenant_id", "domain"])
    op.create_index(
        "uq_tenant_domains_verified_domain",
        "tenant_domains",
        ["domain"],
        unique=True,
        postgresql_where=sa.text(_VERIFIED),
        sqlite_where=sa.text(_VERIFIED),
    )
    op.add_column(
        "sso_connections",
        sa.Column("trust_email_claim", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("refresh_tokens", sa.Column("auth_method", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("refresh_tokens", "auth_method")
    op.drop_column("sso_connections", "trust_email_claim")
    op.drop_index("uq_tenant_domains_verified_domain", table_name="tenant_domains")
    # Restoring global uniqueness needs one row per domain: keep only verified claims.
    op.execute("DELETE FROM tenant_domains WHERE status <> 'verified'")
    with op.batch_alter_table("tenant_domains") as batch:
        batch.drop_constraint("uq_tenant_domains_tenant_id", type_="unique")
        batch.create_unique_constraint("uq_tenant_domains_domain", ["domain"])
