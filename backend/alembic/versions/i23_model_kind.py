"""Model kinds: chat and embedding (feature plan v2 순번 12 B1 minimum, memory plan Q14).

``models.kind`` tells a conversational model from an embedding model; an embedding model
carries the vector width it is asked for (``embedding_dimensions``). Existing rows are
``chat``. Chat surfaces (agents, fallbacks, defaults) never list or bind an embedding
model; memory recall picks one in the company memory settings (``tenants.settings``).
Downgrade deletes embedding models and drops both columns.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "i23_model_kind"
down_revision = "i22_memory_recall"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("models") as batch:
        batch.add_column(sa.Column("kind", sa.String(20), nullable=False, server_default="chat"))
        batch.add_column(sa.Column("embedding_dimensions", sa.Integer(), nullable=True))
        batch.create_check_constraint("ck_models_kind", "kind IN ('chat', 'embedding')")
        batch.create_check_constraint(
            "ck_models_embedding_dimensions",
            "(kind = 'chat' AND embedding_dimensions IS NULL) OR "
            "(kind = 'embedding' AND embedding_dimensions IS NOT NULL"
            " AND embedding_dimensions BETWEEN 1 AND 4096)",
        )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM models WHERE kind = 'embedding'"))
    with op.batch_alter_table("models") as batch:
        batch.drop_constraint("ck_models_embedding_dimensions", type_="check")
        batch.drop_constraint("ck_models_kind", type_="check")
        batch.drop_column("embedding_dimensions")
        batch.drop_column("kind")
