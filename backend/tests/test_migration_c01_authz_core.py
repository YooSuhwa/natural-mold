"""C-01: authorization core tables (plan m84) as the next deployable revision after B11."""

from __future__ import annotations

import hashlib
import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, Connection, create_engine, insert, inspect
from sqlalchemy.exc import IntegrityError

import app.models  # noqa: F401
from alembic import op as alembic_op
from app.database import Base

BACKEND_ROOT = Path(__file__).resolve().parents[1]
VERSIONS = BACKEND_ROOT / "alembic" / "versions"
CORE_TABLES = (
    "resource_grants",
    "authz_outbox",
    "authz_revocations",
    "authz_model_versions",
    "authz_shadow_diffs",
)
B11_SHA256 = "04f504aca05f0d026d5f8f9c8ff26f617a0d3ff0a69109c3189a2138db5e1077"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"{name}_test", VERSIONS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migrated(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Connection, ModuleType]]:
    migration = _load("c01_authz_core")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys = ON")
        prior = [table for table in Base.metadata.sorted_tables if table.name not in CORE_TABLES]
        Base.metadata.create_all(connection, tables=prior)
        context = MigrationContext.configure(connection)
        monkeypatch.setattr(alembic_op, "_proxy", Operations(context), raising=False)
        migration.upgrade()
        yield connection, migration
    engine.dispose()


def test_single_head_chain_follows_b11() -> None:
    script = ScriptDirectory.from_config(Config(str(BACKEND_ROOT / "alembic.ini")))

    assert script.get_heads() == ["c01_marketplace_acl_grants"]
    core = script.get_revision("c01_authz_core")
    grants = script.get_revision("c01_marketplace_acl_grants")
    assert core is not None and grants is not None
    assert core.down_revision == "b11_company_administration"
    assert grants.down_revision == "c01_authz_core"
    assert all(len(rev) <= 32 for rev in ("c01_authz_core", "c01_marketplace_acl_grants"))


def test_b11_revision_is_unchanged() -> None:
    digest = hashlib.sha256((VERSIONS / "b11_company_administration.py").read_bytes())

    assert digest.hexdigest() == B11_SHA256


def test_upgrade_creates_tables_matching_orm_contract(
    migrated: tuple[Connection, ModuleType],
) -> None:
    connection, _ = migrated
    inspector = inspect(connection)

    for name in CORE_TABLES:
        orm = Base.metadata.tables[name]
        reflected = {column["name"]: column for column in inspector.get_columns(name)}
        assert set(reflected) == set(orm.c.keys()), name
        for column in orm.c:
            assert reflected[column.name]["nullable"] is column.nullable, (name, column.name)
        assert inspector.get_pk_constraint(name)["name"] == f"pk_{name}"
        orm_checks = {
            str(constraint.name)
            for constraint in orm.constraints
            if isinstance(constraint, CheckConstraint)
        }
        assert {check["name"] for check in inspector.get_check_constraints(name)} == orm_checks
        assert {index["name"] for index in inspector.get_indexes(name)} == {
            str(index.name) for index in orm.indexes
        }
        orm_fks = {
            (tuple(fk.column_keys), fk.referred_table.name, fk.ondelete)
            for fk in orm.foreign_key_constraints
        }
        reflected_fks = {
            (
                tuple(fk["constrained_columns"]),
                fk["referred_table"],
                (fk.get("options") or {}).get("ondelete"),
            )
            for fk in inspector.get_foreign_keys(name)
        }
        assert reflected_fks == orm_fks, name


def _seed_scope(connection: Connection) -> tuple[object, object, object]:
    tenant, org, user = uuid4(), uuid4(), uuid4()
    tables = Base.metadata.tables
    connection.execute(insert(tables["users"]).values(id=user, email=f"{user}@x.test", name="U"))
    connection.execute(
        insert(tables["tenants"]).values(
            id=tenant, name="T", slug=f"t-{tenant}", secrets_namespace=f"tenants/{tenant}"
        )
    )
    connection.execute(
        insert(tables["organizations"]).values(
            id=org, tenant_id=tenant, name="O", slug=f"o-{org}", is_default=True
        )
    )
    return tenant, org, user


def _grant(tenant: object, org: object, user: object, **changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "id": uuid4(),
        "org_id": org,
        "tenant_id": tenant,
        "resource_type": "skill",
        "resource_id": tenant,
        "relation": "viewer",
        "principal_type": "user",
        "principal_id": user,
    }
    values.update(changes)
    return values


def test_active_grant_uniqueness_and_checks_are_enforced(
    migrated: tuple[Connection, ModuleType],
) -> None:
    connection, _ = migrated
    tenant, org, user = _seed_scope(connection)
    grants = Base.metadata.tables["resource_grants"]
    connection.execute(insert(grants).values(_grant(tenant, org, user)))
    connection.execute(insert(grants).values(_grant(tenant, org, user, state="revoked")))

    invalid = (
        {},
        {"state": "pending"},
        {"resource_type": "folder"},
        {"relation": "owner"},
        {"principal_type": "role"},
        {"grant_kind": "manual"},
        {"relation": "allowed_org", "principal_type": "user"},
        {"org_id": uuid4()},
    )
    for changes in invalid:
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(insert(grants).values(_grant(tenant, org, user, **changes)))

    stored = connection.execute(grants.select()).mappings().all()
    assert {(row["state"], row["grant_kind"]) for row in stored} == {
        ("active", "share"),
        ("revoked", "share"),
    }


def test_downgrade_removes_every_core_table(migrated: tuple[Connection, ModuleType]) -> None:
    connection, migration = migrated

    migration.downgrade()

    assert not set(CORE_TABLES) & set(inspect(connection).get_table_names())
    migration.upgrade()
    assert set(CORE_TABLES) <= set(inspect(connection).get_table_names())
