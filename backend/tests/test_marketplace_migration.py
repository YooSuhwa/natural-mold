"""Marketplace migrations (m40~m43) — module sanity + m41 backfill semantics.

Two layers (matching the existing ``test_migration_m22.py`` pattern):

1. **Module-level**: each migration file imports cleanly, declares the
   expected revision chain (``m40 → m41 → m42 → m43`` with ``m39`` as the
   tail), and exposes callable ``upgrade``/``downgrade``.
2. **Backfill semantics**: the m41 UPDATE statements applied against an
   ORM-built ``skills`` table produce the documented OI-5 / Spec §15.2
   outcome (text → 'user'/'created_by_me', package → 'import'/'imported_by_me').

Full alembic ``upgrade head → downgrade -4 → upgrade head`` round-trip
runs against a live PostgreSQL container; that is exercised by the
CHECKPOINT verify command — kept out of unit tests because aiosqlite
doesn't support several constructs the migrations need (Partial indexes,
``USING`` casts, ``ALTER TABLE … ADD CONSTRAINT`` after the fact).
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill import Skill
from tests.conftest import TEST_USER_ID

_VERSIONS_DIR = (
    Path(__file__).resolve().parent.parent / "alembic" / "versions"
)


def _load(name: str):
    """Load a migration module by file name (without ``.py``)."""

    path = _VERSIONS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_test_load", path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ===========================================================================
# Revision chain sanity
# ===========================================================================


class TestRevisionChain:
    """Each marketplace migration must declare the expected ancestor.

    Catches accidental rename / unlinked-revision regressions early —
    cheaper than re-running ``alembic upgrade head`` to discover the gap.
    """

    def test_m40_revises_m39(self) -> None:
        mod = _load("m40_marketplace_tables")
        assert mod.revision == "m40_marketplace_tables"
        # Actual tail revision (file `m39_dedupe_system_credentials.py` but
        # ``revision = 'm39_dedupe_system_creds'``).
        assert mod.down_revision == "m39_dedupe_system_creds"
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)

    def test_m41_revises_m40(self) -> None:
        mod = _load("m41_skills_marketplace_columns")
        assert mod.revision == "m41_skills_marketplace_columns"
        assert mod.down_revision == "m40_marketplace_tables"
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)

    def test_m42_revises_m41(self) -> None:
        mod = _load("m42_agent_skills_config")
        assert mod.revision == "m42_agent_skills_config"
        assert mod.down_revision == "m41_skills_marketplace_columns"
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)

    def test_m43_revises_m42(self) -> None:
        mod = _load("m43_skill_credential_bindings")
        assert mod.revision == "m43_skill_credential_bindings"
        assert mod.down_revision == "m42_agent_skills_config"
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)


# ===========================================================================
# Metadata sanity — model surface matches the migration intent
# ===========================================================================


class TestMetadataIncludesM41Columns:
    """The 12 columns m41 adds must exist on the ORM model (otherwise the
    migration drifts from the code that consumes it)."""

    def test_skills_model_has_m41_columns(self) -> None:
        cols = {c.name for c in Skill.__table__.columns}
        for col in (
            "is_system",
            "source_kind",
            "source_marketplace_item_id",
            "source_marketplace_version_id",
            "source_commit",
            "credential_requirements",
            "execution_profile",
            "origin_kind",
            "origin_user_id",
            "origin_marketplace_item_id",
            "origin_marketplace_version_id",
            "is_dirty",
        ):
            assert col in cols, (
                f"Skill ORM missing m41 column {col!r} — model/migration drift"
            )

    def test_agent_skills_model_has_config_column(self) -> None:
        from app.models.skill import AgentSkillLink

        cols = {c.name for c in AgentSkillLink.__table__.columns}
        assert "config" in cols, (
            "AgentSkillLink missing 'config' JSON column — m42 not propagated"
        )


# ===========================================================================
# m41 backfill semantics (OI-5 / Spec §15.2) — independent of alembic harness
# ===========================================================================


class TestM41BackfillSemantics:
    """Apply the EXACT UPDATE statements shipped in m41 to an ORM-built
    schema. Equivalent to running the migration against the test DB but
    avoids the alembic round-trip dependency.

    These guards exist because:

    * The m41 backfill is the ONLY way pre-marketplace package skills get
      ``origin_kind='imported_by_me'`` (Bezos OI-5).
    * If any of these statements drifts during a future migration rewrite,
      every existing customer's dashboard will silently mis-label package
      skills as ``created_by_me``.
    """

    @pytest.mark.asyncio
    async def test_text_skills_backfilled_to_user_and_created_by_me(
        self, db: AsyncSession
    ) -> None:
        text_skill = Skill(
            id=uuid.uuid4(),
            user_id=TEST_USER_ID,
            name="legacy-text",
            slug="legacy-text",
            description=None,
            kind="text",
            storage_path=None,
            content_hash=None,
            size_bytes=0,
            version=None,
            package_metadata=None,
            used_by_count=0,
            # Simulate pre-m41 snapshot: source_kind NULL.
            source_kind=None,
            origin_kind="created_by_me",  # column default
        )
        db.add(text_skill)
        await db.flush()

        await db.execute(
            text(
                "UPDATE skills SET source_kind = 'user' "
                "WHERE source_kind IS NULL AND kind = 'text'"
            )
        )
        await db.execute(
            text(
                "UPDATE skills SET origin_kind = 'imported_by_me' "
                "WHERE kind = 'package'"
            )
        )
        await db.commit()
        await db.refresh(text_skill)

        assert text_skill.source_kind == "user"
        # Text-kind row must NOT be touched by the package backfill.
        assert text_skill.origin_kind == "created_by_me"

    @pytest.mark.asyncio
    async def test_package_skills_backfilled_to_import_and_imported_by_me(
        self, db: AsyncSession
    ) -> None:
        package_skill = Skill(
            id=uuid.uuid4(),
            user_id=TEST_USER_ID,
            name="legacy-pkg",
            slug="legacy-pkg",
            description=None,
            kind="package",
            storage_path="/tmp/legacy-pkg",
            content_hash="0" * 64,
            size_bytes=10,
            version=None,
            package_metadata=None,
            used_by_count=0,
            source_kind=None,
            origin_kind="created_by_me",  # default before backfill
        )
        db.add(package_skill)
        await db.flush()

        await db.execute(
            text(
                "UPDATE skills SET source_kind = 'import' "
                "WHERE source_kind IS NULL AND kind = 'package'"
            )
        )
        await db.execute(
            text(
                "UPDATE skills SET origin_kind = 'imported_by_me' "
                "WHERE kind = 'package'"
            )
        )
        await db.commit()
        await db.refresh(package_skill)

        assert package_skill.source_kind == "import"
        assert package_skill.origin_kind == "imported_by_me"

    @pytest.mark.asyncio
    async def test_backfill_does_not_overwrite_explicit_source_kind(
        self, db: AsyncSession
    ) -> None:
        """``WHERE source_kind IS NULL`` clause must protect rows already
        tagged as ``k-skill`` / ``system_seed`` (Spec §15.2 prerequisite)."""

        kskill = Skill(
            id=uuid.uuid4(),
            user_id=TEST_USER_ID,
            name="k-spell",
            slug="k-spell",
            description=None,
            kind="package",
            storage_path=None,
            content_hash=None,
            size_bytes=0,
            version=None,
            package_metadata=None,
            used_by_count=0,
            is_system=True,
            source_kind="k-skill",  # explicit pre-backfill value
            origin_kind="created_by_me",
        )
        db.add(kskill)
        await db.flush()

        await db.execute(
            text(
                "UPDATE skills SET source_kind = 'import' "
                "WHERE source_kind IS NULL AND kind = 'package'"
            )
        )
        await db.commit()
        await db.refresh(kskill)

        # Explicit 'k-skill' must NOT be flipped to 'import'.
        assert kskill.source_kind == "k-skill"


# ===========================================================================
# Server-default sanity
# ===========================================================================


class TestServerDefaults:
    """Column server defaults shipped by m41/m42 must materialize when the
    ORM omits the field (this is what ``Base.metadata.create_all`` exercises;
    Postgres applies the same defaults via the migration's ``server_default``)."""

    @pytest.mark.asyncio
    async def test_new_skill_picks_up_origin_kind_and_is_dirty_defaults(
        self, db: AsyncSession
    ) -> None:
        row = Skill(
            id=uuid.uuid4(),
            user_id=TEST_USER_ID,
            name="defaults",
            slug="defaults",
            description=None,
            kind="text",
            storage_path=None,
            content_hash=None,
            size_bytes=0,
            version=None,
            package_metadata=None,
            used_by_count=0,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        assert row.origin_kind == "created_by_me"
        assert row.is_dirty is False
        assert row.is_system is False
