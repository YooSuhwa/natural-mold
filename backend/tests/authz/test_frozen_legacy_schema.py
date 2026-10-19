"""Draft migration fixtures cannot drift when live ORM scope columns are promoted."""

import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter
from sqlalchemy import MetaData
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateIndex, CreateTable

from app.database import Base
from schema_drafts.org_authz.schema import baseline_metadata


def ddl_signature(metadata: MetaData) -> tuple[str, ...]:
    statements: list[str] = []
    for dialect in (postgresql.dialect(), sqlite.dialect()):
        for name in sorted(metadata.tables):
            table = metadata.tables[name]
            statements.append(str(CreateTable(table).compile(dialect=dialect)))
            statements.extend(
                str(CreateIndex(index).compile(dialect=dialect))
                for index in sorted(table.indexes, key=lambda index: index.name or "")
            )
    return tuple(statements)


def test_live_orm_changes_do_not_modify_the_m77_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the historical fixture's exact baseline DDL before a live model promotion.
    original = ddl_signature(baseline_metadata())
    # When: the live ORM changes a legacy column constraint.
    monkeypatch.setattr(Base.metadata.tables["conversations"].c.title, "nullable", False)
    # Then: the immutable baseline and both dialects retain their original definition.
    assert ddl_signature(baseline_metadata()) == original
    assert baseline_metadata().tables["conversations"].c.title.nullable is True


def test_each_baseline_request_returns_an_independent_metadata_copy() -> None:
    # Given: one caller's baseline copy.
    first = baseline_metadata()
    # When: that caller modifies it for a draft migration.
    first.tables["conversations"].c.title.nullable = False
    # Then: later callers still get the historical 63 tables and nullable title.
    second = baseline_metadata()
    assert len(second.tables) == 63
    assert second.tables["conversations"].c.title.nullable is True


def test_frozen_ddl_matches_the_captured_pre_promotion_sql_for_both_dialects() -> None:
    # Given: the original pre-promotion PostgreSQL/SQLite DDL, checked in once.
    path = Path(__file__).resolve().parents[2] / "schema_drafts/org_authz/m77/ddl_contract.json"
    original = TypeAdapter(tuple[str, ...]).validate_python(json.loads(path.read_text()))
    # When: the frozen definitions are compiled independently of live models.
    current = ddl_signature(baseline_metadata())
    # Then: SQL definitions match; constraint ordering within CREATE TABLE is immaterial.
    assert len(current) == len(original) == 282
    for expected, actual in zip(original, current, strict=True):
        expected_lines = sorted(
            line.strip().rstrip(",") for line in expected.splitlines() if line.strip()
        )
        actual_lines = sorted(
            line.strip().rstrip(",") for line in actual.splitlines() if line.strip()
        )
        assert actual_lines == expected_lines
