"""Compare promoted DDL/data contracts against the independently retained review drafts."""

from collections.abc import Callable

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import JsonValue
from sqlalchemy.engine import Connection

from app.migrations.org_authz_v1.metadata import migration_context
from app.migrations.org_authz_v1.snapshot_io import load
from schema_drafts.org_authz import m81_org_scope_columns as draft_m81
from schema_drafts.org_authz import m82_default_org_backfill as draft_m82
from schema_drafts.org_authz import m83_org_scope_not_null as draft_m83
from schema_drafts.org_authz.schema import schema_metadata
from tests.authz.org_authz_b02_fixture import seed
from tests.authz.scope_migration_fixture import execute, prepare_m80, relational_contract


def execute_draft(connection: Connection, operation: Callable[[], None]) -> None:
    with Operations.context(MigrationContext.configure(connection)):
        operation()


def without_generated_dates(value: JsonValue) -> JsonValue:
    match value:
        case dict():
            return {
                key: sorted(cell, key=str)
                if key == "columns"
                and isinstance(cell, list)
                and all(isinstance(column, str) for column in cell)
                else without_generated_dates(cell)
                for key, cell in value.items()
                if key not in {"created_at", "updated_at", "joined_at", "granted_at"}
            }
        case list():
            return [without_generated_dates(cell) for cell in value]
        case _:
            return value


def assert_json_parity(source: JsonValue, target: JsonValue, path: str) -> None:
    if isinstance(source, dict) and isinstance(target, dict):
        assert source.keys() == target.keys(), path
        for key, value in source.items():
            assert_json_parity(value, target[key], f"{path}.{key}")
    elif isinstance(source, list) and isinstance(target, list):
        assert len(source) == len(target), path
        for index, (left, right) in enumerate(zip(source, target, strict=True)):
            assert_json_parity(left, right, f"{path}[{index}]")
    else:
        assert source == target, path


def test_promoted_schema_and_v2_backfill_match_frozen_review_contract() -> None:
    # Given: identical legacy schemas and seed identities in independent databases.
    results: list[tuple[dict[str, object], JsonValue, dict[str, object]]] = []
    for promoted in (False, True):
        engine = sa.create_engine("sqlite://")
        try:
            with engine.begin() as connection:
                prepare_m80(connection)
                if promoted:
                    execute(connection, "m81_org_scope_columns")
                else:
                    execute_draft(connection, draft_m81.upgrade)
                m81_schema = relational_contract(connection)
                seed(connection, schema_metadata(scope_columns=True))
                if promoted:
                    execute(connection, "m82_default_org_backfill")
                else:
                    execute_draft(connection, draft_m82.upgrade)
                snapshot = load(migration_context(connection))
                assert snapshot is not None
                # When: the same source records are scoped by each implementation.
                normalized = without_generated_dates(snapshot.model_dump(mode="json"))
                if promoted:
                    execute(connection, "m83_org_scope_not_null")
                else:
                    execute_draft(connection, draft_m83.upgrade)
                results.append((m81_schema, normalized, relational_contract(connection)))
        finally:
            engine.dispose()
    # Then: DDL semantics and every captured original/expected row and dependency match.
    assert results[0][0] == results[1][0]
    assert results[0][2] == results[1][2]
    assert_json_parity(results[0][1], results[1][1], "snapshot")
