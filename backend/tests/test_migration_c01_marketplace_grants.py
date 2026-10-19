"""C-01: legacy marketplace ACL and visibility copied into resource_grants (plan m85, 10.5)."""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Connection, create_engine, func, insert, select

import app.models  # noqa: F401
from alembic import op as alembic_op
from app.database import Base

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
CORE_TABLES = (
    "resource_grants",
    "authz_outbox",
    "authz_revocations",
    "authz_model_versions",
    "authz_shadow_diffs",
)
RELATION = {"view": "viewer", "install": "installer", "manage": "manager"}

type Grant = tuple[UUID, str, str, UUID, UUID, UUID]


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"{name}_test", VERSIONS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass
class Lane:
    connection: Connection
    migration: ModuleType
    tenant: UUID
    orgs: tuple[UUID, UUID]
    users: tuple[UUID, UUID, UUID]


@pytest.fixture
def lane(monkeypatch: pytest.MonkeyPatch) -> Iterator[Lane]:
    engine = create_engine("sqlite://")
    tables = Base.metadata.tables
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys = ON")
        prior = [table for table in Base.metadata.sorted_tables if table.name not in CORE_TABLES]
        Base.metadata.create_all(connection, tables=prior)
        context = MigrationContext.configure(connection)
        monkeypatch.setattr(alembic_op, "_proxy", Operations(context), raising=False)
        _load("c01_authz_core").upgrade()
        tenant = uuid4()
        orgs = (uuid4(), uuid4())
        users = (uuid4(), uuid4(), uuid4())
        connection.execute(
            insert(tables["tenants"]).values(
                id=tenant, name="T", slug="t", secrets_namespace=f"tenants/{tenant}"
            )
        )
        for index, org in enumerate(orgs):
            connection.execute(
                insert(tables["organizations"]).values(
                    id=org, tenant_id=tenant, name=f"O{index}", slug=f"o{index}"
                )
            )
        for user in users:
            connection.execute(
                insert(tables["users"]).values(id=user, email=f"{user}@x.test", name="U")
            )
        yield Lane(connection, _load("c01_marketplace_acl_grants"), tenant, orgs, users)
    engine.dispose()


def _item(lane: Lane, visibility: str, *, org: UUID | None = None) -> UUID:
    item = uuid4()
    system = visibility == "system"
    lane.connection.execute(
        insert(Base.metadata.tables["marketplace_items"]).values(
            id=item,
            resource_type="skill",
            owner_user_id=None if system else lane.users[0],
            org_id=None if system else (org or lane.orgs[0]),
            tenant_id=None if system else lane.tenant,
            is_system=system,
            name=visibility,
            slug=f"{visibility}-{item}",
            visibility=visibility,
        )
    )
    return item


def _acl(lane: Lane, item: UUID, user: UUID, permission: str) -> None:
    lane.connection.execute(
        insert(Base.metadata.tables["marketplace_item_acl"]).values(
            item_id=item, user_id=user, permission=permission
        )
    )


def _grants(connection: Connection) -> set[Grant]:
    grants = Base.metadata.tables["resource_grants"]
    rows = connection.execute(
        select(
            grants.c.resource_id,
            grants.c.relation,
            grants.c.principal_type,
            grants.c.principal_id,
            grants.c.org_id,
            grants.c.tenant_id,
        ).where(
            grants.c.resource_type == "marketplace_item",
            grants.c.grant_kind == "migration",
            grants.c.state == "active",
        )
    ).all()
    return {(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows}


def test_restricted_acl_row_count_equals_migrated_user_grant_count(lane: Lane) -> None:
    first, second = _item(lane, "restricted"), _item(lane, "restricted", org=lane.orgs[1])
    expected: set[Grant] = set()
    for item, org in ((first, lane.orgs[0]), (second, lane.orgs[1])):
        for user, permission in zip(lane.users, ("view", "install", "manage"), strict=True):
            _acl(lane, item, user, permission)
            expected.add((item, RELATION[permission], "user", user, org, lane.tenant))
    acl_rows = lane.connection.scalar(
        select(func.count()).select_from(Base.metadata.tables["marketplace_item_acl"])
    )

    lane.migration.upgrade()

    observed = _grants(lane.connection)
    assert observed == expected
    assert acl_rows == len(observed) == 6


def test_visibility_projection_matches_mapping_table_exactly(lane: Lane) -> None:
    private, public = _item(lane, "private"), _item(lane, "public")
    unlisted, system = _item(lane, "unlisted"), _item(lane, "system")
    _acl(lane, private, lane.users[1], "view")
    _acl(lane, private, lane.users[2], "manage")
    _acl(lane, public, lane.users[1], "install")
    _acl(lane, public, lane.users[2], "manage")
    org, tenant = lane.orgs[0], lane.tenant

    lane.migration.upgrade()

    assert _grants(lane.connection) == {
        (public, "installer", "organization", org, org, tenant),
        (public, "manager", "user", lane.users[2], org, tenant),
        (unlisted, "installer", "organization", org, org, tenant),
        *((system, "installer", "organization", each, each, tenant) for each in lane.orgs),
    }


def test_system_item_acl_aborts_instead_of_dropping_management(lane: Lane) -> None:
    _acl(lane, _item(lane, "system"), lane.users[1], "manage")

    with pytest.raises(ValueError, match="delete that marketplace_item_acl row") as error:
        lane.migration.upgrade()

    assert "system marketplace item " in str(error.value)
    assert "change the item visibility" in str(error.value)


def test_cross_company_restricted_acl_users_are_copied_and_counted(
    lane: Lane, caplog: pytest.LogCaptureFixture
) -> None:
    item = _item(lane, "restricted")
    lane.connection.execute(
        insert(Base.metadata.tables["tenant_members"]).values(
            tenant_id=lane.tenant, user_id=lane.users[1]
        )
    )
    _acl(lane, item, lane.users[1], "view")
    _acl(lane, item, lane.users[2], "install")
    org, tenant = lane.orgs[0], lane.tenant

    with caplog.at_level("WARNING", logger="alembic.runtime.migration"):
        lane.migration.upgrade()

    assert _grants(lane.connection) == {
        (item, "viewer", "user", lane.users[1], org, tenant),
        (item, "installer", "user", lane.users[2], org, tenant),
    }
    assert [r.getMessage() for r in caplog.records if "m85" in r.getMessage()] == [
        "m85 copied 1 restricted ACL rows whose user is not an active member of the "
        "item's company; reconcile must review them"
    ]


def test_rerun_is_identical_and_downgrade_keeps_legacy_acl(lane: Lane) -> None:
    restricted = _item(lane, "restricted")
    _acl(lane, restricted, lane.users[1], "install")
    _item(lane, "public")
    grants = Base.metadata.tables["resource_grants"]
    share = {
        "id": uuid4(),
        "org_id": lane.orgs[0],
        "tenant_id": lane.tenant,
        "resource_type": "marketplace_item",
        "resource_id": restricted,
        "relation": "viewer",
        "principal_type": "user",
        "principal_id": lane.users[2],
        "grant_kind": "share",
    }
    lane.connection.execute(insert(grants).values(share))

    lane.migration.upgrade()
    first = lane.connection.execute(select(grants.c.id).order_by(grants.c.id)).scalars().all()
    lane.migration.upgrade()
    second = lane.connection.execute(select(grants.c.id).order_by(grants.c.id)).scalars().all()
    lane.migration.downgrade()

    assert first == second and len(first) == 3
    assert lane.connection.execute(select(grants.c.id)).scalars().all() == [share["id"]]
    acl = Base.metadata.tables["marketplace_item_acl"]
    assert lane.connection.execute(select(acl.c.item_id, acl.c.permission)).all() == [
        (restricted, "install")
    ]
