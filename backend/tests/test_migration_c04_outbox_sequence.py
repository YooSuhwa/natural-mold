"""C-04: ``authz_outbox.seq``/``tuple_key`` revision — backfill order, contract, roundtrip."""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from uuid import UUID

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, create_engine, inspect, text

import app.models  # noqa: F401
from alembic import op as alembic_op
from app.database import Base

BACKEND_ROOT = Path(__file__).resolve().parents[1]
VERSIONS = BACKEND_ROOT / "alembic" / "versions"
CORE = {"resource_grants", "authz_outbox", "authz_revocations", "authz_model_versions"}
CORE |= {"authz_shadow_diffs"}
T0 = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"{name}_c04", VERSIONS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def at_c01(monkeypatch: pytest.MonkeyPatch) -> Iterator[Connection]:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        prior = [table for table in Base.metadata.sorted_tables if table.name not in CORE]
        Base.metadata.create_all(connection, tables=prior)
        context = MigrationContext.configure(connection)
        monkeypatch.setattr(alembic_op, "_proxy", Operations(context), raising=False)
        _load("c01_authz_core").upgrade()
        yield connection
    engine.dispose()


def _old_row(connection: Connection, row_id: int, created_at: datetime, obj: str) -> None:
    connection.execute(
        text(
            "INSERT INTO authz_outbox (id, created_at, op, tuple, source_table, source_id) "
            "VALUES (:id, :created_at, 'write', :tuple, 'resource_grants', :id)"
        ),
        {
            "id": UUID(int=row_id).hex,
            "created_at": created_at,
            "tuple": f'{{"user": "user:u", "relation": "viewer", "object": "{obj}"}}',
        },
    )


def test_single_head_is_the_c04_revision() -> None:
    script = ScriptDirectory.from_config(Config(str(BACKEND_ROOT / "alembic.ini")))
    (head,) = script.get_heads()  # later revisions (D-02 onward) build on c04
    assert "c04_outbox_sequence" in {r.revision for r in script.iterate_revisions(head, "base")}
    revision = script.get_revision("c04_outbox_sequence")
    assert revision is not None and revision.down_revision == "c01_marketplace_acl_grants"


def test_backfill_follows_created_at_then_id_and_stores_the_key(at_c01: Connection) -> None:
    _old_row(at_c01, 2, T0, "agent:b")
    _old_row(at_c01, 1, T0, "agent:a")
    _old_row(at_c01, 3, T0 - timedelta(seconds=1), "agent:c")
    _load("c04_outbox_sequence").upgrade()
    rows = at_c01.execute(text("SELECT tuple_key, seq FROM authz_outbox ORDER BY seq")).all()
    assert [tuple(row) for row in rows] == [
        ("user:u viewer agent:c", 1),
        ("user:u viewer agent:a", 2),
        ("user:u viewer agent:b", 3),
    ]


def test_upgraded_table_matches_the_orm_and_downgrade_restores_c01(at_c01: Connection) -> None:
    migration = _load("c04_outbox_sequence")
    migration.upgrade()
    inspector = inspect(at_c01)
    orm = Base.metadata.tables["authz_outbox"]
    reflected = {column["name"]: column for column in inspector.get_columns("authz_outbox")}
    assert set(reflected) == set(orm.c.keys())
    assert reflected["seq"]["nullable"] is False and reflected["tuple_key"]["nullable"] is False
    indexes = {index["name"]: index for index in inspector.get_indexes("authz_outbox")}
    assert set(indexes) == {str(index.name) for index in orm.indexes}
    assert indexes["uq_authz_outbox_seq"]["unique"]
    migration.downgrade()
    columns = {column["name"] for column in inspect(at_c01).get_columns("authz_outbox")}
    assert not {"seq", "tuple_key"} & columns
    assert "uq_authz_outbox_seq" not in {
        index["name"] for index in inspect(at_c01).get_indexes("authz_outbox")
    }
    migration.upgrade()  # repeatable
    assert {"seq", "tuple_key"} <= {c["name"] for c in inspect(at_c01).get_columns("authz_outbox")}
