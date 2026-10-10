"""A13 source inventory versus actual Alembic DDL, including deferred cleanup."""

from pathlib import Path

import pytest
from sqlalchemy import MetaData, create_engine

from app.database import Base
from schema_drafts.org_authz.chain import DRAFTS
from schema_drafts.org_authz.event_contract import missing_sources
from schema_drafts.org_authz.plan_source import source_new_table_codes, source_rows
from schema_drafts.org_authz.schema import NEW_TABLES
from schema_drafts.org_authz.schema_contract import schema_contract, table_inventory
from tests.authz.test_full_draft_chain import execute_draft

PLAN = Path(__file__).parents[3] / "docs/exec-plans/org-authz-plan.html"


@pytest.fixture(scope="module")
def migrated_metadata() -> MetaData:
    """Own one disposable SQLite database and reflect real m78–m89 operations."""
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            for draft in DRAFTS[:12]:
                execute_draft(connection, draft)
            metadata = MetaData()
            metadata.reflect(connection)
            return metadata
    finally:
        engine.dispose()


def test_inventory_pins_original_schema_sections_and_all_new_tables() -> None:
    # Given: original source sections and the reviewed field-level inventory.
    contract = schema_contract()
    # When: source tables are independently parsed from preserved HTML.
    rows = tuple(source_rows(PLAN, section) for section in ("6.1", "6.2", "6.3"))
    # Then: source edits require deliberate re-review rather than silent regeneration.
    assert rows == (contract.source_6_1, contract.source_6_2, contract.source_6_3)
    assert frozenset(contract.new_tables) == NEW_TABLES
    assert len(contract.new_tables) == 22
    assert source_new_table_codes(PLAN) == contract.source_6_1_codes
    source_names = set(contract.source_6_1_codes) | {row[0] for row in contract.source_6_1}
    assert set(contract.new_tables) <= source_names


def test_reflected_m89_schema_matches_reviewed_field_contract(migrated_metadata: MetaData) -> None:
    # Given: the fixed contract is independent of today's migration implementation.
    expected = schema_contract().tables
    # When: actual reflected fields/FKs/defaults/checks/indexes are canonicalized.
    actual = table_inventory(migrated_metadata)
    # Then: differences cannot hide behind table counts or create_all-only evidence.
    assert actual == expected


def test_every_event_source_column_exists_in_actual_schema(migrated_metadata: MetaData) -> None:
    # Given: real upgraded DDL and all DB/tuple/marker/epoch sources for 19 events.
    # When: the crosswalk checks concrete column references against reflection.
    missing = missing_sources(migrated_metadata)
    # Then: no invented names or fields absent from the final stage are accepted.
    assert missing == ()


def test_source_guard_detects_a_missing_projection_table(migrated_metadata: MetaData) -> None:
    # Given: the same schema with the required delegation source omitted.
    metadata = MetaData()
    for table in migrated_metadata.tables.values():
        if table.name != "resource_grants":
            table.to_metadata(metadata)
    # When: all event source references are checked.
    missing = missing_sources(metadata)
    # Then: a schema without the grant source is rejected explicitly.
    assert missing == ("resource_grants",)


def test_inventory_detects_changed_nullability(migrated_metadata: MetaData) -> None:
    # Given: a schema that wrongly makes archived_at required for every agent.
    metadata = MetaData()
    for table in migrated_metadata.tables.values():
        table.to_metadata(metadata)
    metadata.tables["agents"].c.archived_at.nullable = False
    # When: the altered schema is compared with the independently stored contract.
    actual = table_inventory(metadata)
    # Then: field-level drift fails despite identical table and column counts.
    assert actual != schema_contract().tables


def test_deferred_cleanup_preserves_all_event_sources() -> None:
    # Given: explicit disposable validation, with no production receipt manufactured.
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            # When: all drafts, including operationally gated cleanup, run locally.
            for draft in DRAFTS:
                execute_draft(connection, draft, disposable=True)
            metadata = MetaData()
            metadata.reflect(connection)
            # Then: original-source-only fields disappear, event sources remain.
            assert missing_sources(metadata) == ()
            assert "marketplace_item_acl" not in metadata.tables
            assert "authz_shadow_diffs" not in metadata.tables
            assert "is_shared" not in metadata.tables["credentials"].c
            assert "is_favorite" not in metadata.tables["agents"].c
    finally:
        engine.dispose()
