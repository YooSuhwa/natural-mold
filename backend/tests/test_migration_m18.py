"""m18 greenfield migration sanity tests.

We do not run ``alembic upgrade head`` end-to-end against sqlite — the
historical chain (m1~m17) contains Postgres-specific operations that the
test runner cannot replay. Instead, this test verifies:

1. The migration module imports cleanly and exposes ``upgrade``/``downgrade``.
2. ``downgrade`` raises ``NotImplementedError`` (intentional non-reversibility).
3. The model metadata produced by ``Base.metadata`` matches the table set the
   migration creates — guaranteeing future ``Base.metadata.create_all`` and
   the migration stay in sync.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.database import Base

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "m18_greenfield_credentials.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "m18_greenfield_credentials_test_load", _MIGRATION
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_module_imports() -> None:
    mod = _load_module()
    assert mod.revision == "m18_greenfield_credentials"
    assert mod.down_revision == "m17_add_agent_subagents"
    assert callable(mod.upgrade)
    assert callable(mod.downgrade)


def test_downgrade_is_non_reversible() -> None:
    mod = _load_module()
    with pytest.raises(NotImplementedError):
        mod.downgrade()


def test_metadata_includes_greenfield_tables() -> None:
    """The new ORM modules must contribute the expected tables."""

    expected = {
        "credentials",
        "credential_audit_logs",
        "credential_defaults",
        "tools",
        "mcp_servers",
        "mcp_tools",
        "skills",
        "models",
        "agent_tools",
        "agent_skills",
    }
    table_names = set(Base.metadata.tables.keys())
    missing = expected - table_names
    assert not missing, f"missing greenfield tables in metadata: {missing}"


def test_agents_has_llm_credential_id_fk() -> None:
    agents = Base.metadata.tables["agents"]
    assert "llm_credential_id" in agents.columns
    fks = list(agents.columns["llm_credential_id"].foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "credentials"


def test_skills_columns_present() -> None:
    skills = Base.metadata.tables["skills"]
    expected_cols = {
        "id",
        "user_id",
        "name",
        "slug",
        "description",
        "kind",
        "storage_path",
        "content_hash",
        "size_bytes",
        "version",
        "package_metadata",
        "used_by_count",
        "last_modified_at",
        "created_at",
        "updated_at",
    }
    assert expected_cols.issubset(set(skills.columns.keys()))
