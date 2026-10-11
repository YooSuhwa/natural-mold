"""Exact-20 live ORM parity against the independently executed frozen m83 DDL."""

import sqlalchemy as sa

from app.database import Base
from app.migrations.org_authz_v1.contracts import M81_SCOPED
from tests.authz.scope_migration_fixture import execute, prepare_m80


def _foreign_keys(table: sa.Table) -> set[tuple[tuple[str, ...], tuple[str, ...], str | None]]:
    return {
        (
            tuple(element.parent.name for element in constraint.elements),
            tuple(element.target_fullname for element in constraint.elements),
            constraint.ondelete,
        )
        for constraint in table.foreign_key_constraints
    }


def _scope_checks(table: sa.Table) -> set[tuple[str, str]]:
    return {
        (str(constraint.name), "".join(str(constraint.sqltext).split()).lower())
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
        and constraint.name is not None
        and (
            "org_" in str(constraint.name)
            or "tenant" in str(constraint.name)
            or str(constraint.name) in {"ck_credentials_scope", "ck_agents_monthly_spend_limit"}
        )
    }


def test_exact_twenty_private_models_match_frozen_m83_columns_scope_and_foreign_keys() -> None:
    # Given the independently executed, frozen deployable scope migration chain.
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            prepare_m80(connection)
            for revision in (
                "m81_org_scope_columns",
                "m82_default_org_backfill",
                "m83_org_scope_not_null",
            ):
                execute(connection, revision)
            expected = sa.MetaData()
            expected.reflect(connection, only=M81_SCOPED)
        # When each of the exact twenty deployed scoped tables is compared with live ORM.
        assert len(set(M81_SCOPED)) == 20
        for name in M81_SCOPED:
            source, live = expected.tables[name], Base.metadata.tables[name]
            # Then all physical columns, nullability and FK delete policies agree.
            assert set(source.c.keys()) == set(live.c.keys()), name
            assert {column.name: column.nullable for column in source.c} == {
                column.name: column.nullable for column in live.c
            }, name
            assert _foreign_keys(source) == _foreign_keys(live), name
            assert _scope_checks(source) == _scope_checks(live), name
            for key in ("org_id", "tenant_id"):
                assert live.c[key].default is None and live.c[key].server_default is None, name
    finally:
        engine.dispose()
