"""Token usage records whose key paid for it (personal-tenant plan P2, Q2).

``token_usages.llm_credential_scope``: ``personal`` (a user's own key) or ``tenant`` (a
company shared key designated to the run's organization). Existing rows stay NULL
(unknown); keyless runs (the scripted dev model) are NULL too. Downgrade drops the column.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "i09_token_usage_credential_scope"
down_revision = "i08_outbox_request_enqueue"
branch_labels = None
depends_on = None

_TABLE = "token_usages"
_CHECK = "ck_token_usages_llm_credential_scope"


def upgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.add_column(sa.Column("llm_credential_scope", sa.String(20), nullable=True))
        batch.create_check_constraint(
            _CHECK, "llm_credential_scope IS NULL OR llm_credential_scope IN ('personal','tenant')"
        )


def downgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(_CHECK, type_="check")
        batch.drop_column("llm_credential_scope")
