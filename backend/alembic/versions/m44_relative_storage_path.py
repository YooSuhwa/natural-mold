"""M44: clean up absolute ``storage_path`` rows (ADR-018).

Revision ID: m44_relative_storage_path
Revises: m43_skill_credential_bindings
Create Date: 2026-05-23

ADR-018 changes the semantics of ``skills.storage_path`` and
``marketplace_versions.storage_path``: they are now stored *relative to*
``settings.data_root`` so worktree- or deploy-local absolute paths can
never poison the DB again. The 2026-05-23 data-loss incident wiped 2
skill rows and 93 marketplace_version rows whose paths pointed into a
deleted worktree directory.

This migration deletes any row whose ``storage_path`` is still absolute
(starts with ``/``) or contains the worktree fragment ``/worktrees/``.
After M44, all surviving rows must hold relative POSIX paths. Code in
``app.skills.service`` / ``app.marketplace.*`` enforces this at write
time via ``app.storage.paths.ensure_relative``.

Downgrade is a no-op: deleted rows cannot be reconstructed and the
schema itself is unchanged. Operators rerun ``sync_k_skill`` and ask
users to re-import / re-publish.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m44_relative_storage_path"
down_revision = "m43_skill_credential_bindings"
branch_labels = None
depends_on = None


# Tables whose rows may transitively reference an about-to-be-deleted
# skill or marketplace_version. We clean them in dependency order before
# deleting the parent so FK CASCADE / RESTRICT both behave.
_ABSOLUTE_PREDICATE = (
    "storage_path LIKE '/%' OR storage_path LIKE '%/worktrees/%'"
)


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Drop marketplace activity that references doomed versions/skills.
    #    These tables exist only when m40~m43 applied — guard with a
    #    table check to keep the migration runnable on partial schemas.
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    # marketplace_installations.installed_skill_id → skills.id (CASCADE)
    # marketplace_publication_links.source_skill_id → skills.id (CASCADE)
    # marketplace_item_acl, marketplace_versions, marketplace_items have
    # no skills FK but their snapshots live on the same broken filesystem
    # — wipe everything per ADR-018 §3.3 "clean slate" decision.
    if "marketplace_installations" in tables:
        bind.execute(sa.text("DELETE FROM marketplace_installations"))
    if "marketplace_publication_links" in tables:
        bind.execute(sa.text("DELETE FROM marketplace_publication_links"))
    if "marketplace_item_acl" in tables:
        bind.execute(sa.text("DELETE FROM marketplace_item_acl"))
    if "marketplace_versions" in tables:
        bind.execute(sa.text("DELETE FROM marketplace_versions"))
    if "marketplace_items" in tables:
        bind.execute(sa.text("DELETE FROM marketplace_items"))

    # skill_credential_bindings is per-skill; clean the doomed skills'
    # bindings explicitly so CASCADE doesn't have to.
    if "skill_credential_bindings" in tables:
        bind.execute(
            sa.text(
                f"""
                DELETE FROM skill_credential_bindings
                WHERE skill_id IN (
                    SELECT id FROM skills WHERE {_ABSOLUTE_PREDICATE}
                )
                """
            )
        )

    # agent_skills.skill_id → skills.id (CASCADE) — DELETE on skills
    # will follow, but doing it here keeps the SQL self-documenting.
    if "agent_skills" in tables:
        bind.execute(
            sa.text(
                f"""
                DELETE FROM agent_skills
                WHERE skill_id IN (
                    SELECT id FROM skills WHERE {_ABSOLUTE_PREDICATE}
                )
                """
            )
        )

    # 2. Finally drop the skill rows themselves.
    if "skills" in tables:
        bind.execute(sa.text(f"DELETE FROM skills WHERE {_ABSOLUTE_PREDICATE}"))


def downgrade() -> None:
    # ADR-018 §7 — deleted rows cannot be reconstructed. No-op.
    pass
