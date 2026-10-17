"""Native PostgreSQL roundtrip for C-01 authz core tables and marketplace ACL grant copy."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, bindparam, inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from tests.authz.company_administration_migration_fixture import (
    company_migration_lane,
    seed_company,
)
from tests.authz.organization_migration_fixture import alembic_head

pytestmark = pytest.mark.integration

CORE_TABLES = {
    "resource_grants",
    "authz_outbox",
    "authz_revocations",
    "authz_model_versions",
    "authz_shadow_diffs",
}
INSERT_ITEM = text(
    "INSERT INTO marketplace_items (id,resource_type,owner_user_id,org_id,tenant_id,is_system,"
    "is_listed,name,slug,visibility,status,moderation_status,created_at,updated_at) VALUES "
    "(:id,'skill',:owner,:org,:tenant,:system,true,:name,:slug,:visibility,'published',"
    "'approved',now(),now())"
)
INSERT_ACL = text(
    "INSERT INTO marketplace_item_acl (item_id,user_id,permission,created_at) "
    "VALUES (:item,:user,:permission,now())"
)
SELECT_GRANTS = text(
    "SELECT resource_id,relation,principal_type,principal_id,org_id,tenant_id "
    "FROM resource_grants WHERE grant_kind='migration' AND state='active' "
    "AND resource_id IN :items"
).bindparams(bindparam("items", expanding=True))


def _user(connection: Connection) -> UUID:
    user = uuid4()
    connection.execute(
        text("INSERT INTO users (id,email,name,created_at) VALUES (:id,:email,'U',now())"),
        {"id": user, "email": f"{user}@example.test"},
    )
    return user


def _item(connection: Connection, visibility: str, owner: UUID, org: UUID, tenant: UUID) -> UUID:
    item = uuid4()
    system = visibility == "system"
    connection.execute(
        INSERT_ITEM,
        {
            "id": item,
            "owner": None if system else owner,
            "org": None if system else org,
            "tenant": None if system else tenant,
            "system": system,
            "name": visibility,
            "slug": f"{visibility}-{item}",
            "visibility": visibility,
        },
    )
    return item


def test_native_acl_copy_roundtrip_and_constraints() -> None:
    with company_migration_lane() as lane:
        command.downgrade(lane.config, "b11_company_administration")
        with lane.engine.begin() as connection:
            assert not CORE_TABLES & set(inspect(connection).get_table_names())
            seed = seed_company(connection)
            org, tenant = seed.org_id, seed.tenant_id
            viewer, manager = _user(connection), _user(connection)
            items = {
                visibility: _item(connection, visibility, seed.user_id, org, tenant)
                for visibility in ("private", "restricted", "public", "system")
            }
            acl_rows = (
                (items["restricted"], viewer, "view"),
                (items["restricted"], manager, "manage"),
                (items["public"], manager, "manage"),
                (items["private"], viewer, "view"),
                (items["private"], manager, "manage"),
            )
            for item, user, permission in acl_rows:
                connection.execute(
                    INSERT_ACL, {"item": item, "user": user, "permission": permission}
                )

        command.upgrade(lane.config, "head")
        with lane.engine.begin() as connection:
            orgs = connection.execute(text("SELECT id,tenant_id FROM organizations")).all()
            observed = set(connection.execute(SELECT_GRANTS, {"items": list(items.values())}).all())
            assert observed == {
                (items["restricted"], "viewer", "user", viewer, org, tenant),
                (items["restricted"], "manager", "user", manager, org, tenant),
                (items["public"], "manager", "user", manager, org, tenant),
                (items["public"], "installer", "organization", org, org, tenant),
                *((items["system"], "installer", "organization", o, o, t) for o, t in orgs),
            }
            duplicate = {
                "id": uuid4(),
                "org": org,
                "tenant": tenant,
                "item": items["restricted"],
                "user": viewer,
            }
            insert_grant = text(
                "INSERT INTO resource_grants (id,org_id,tenant_id,resource_type,resource_id,"
                "relation,principal_type,principal_id) VALUES "
                "(:id,:org,:tenant,'marketplace_item',:item,'viewer','user',:user)"
            )
            with (
                pytest.raises(IntegrityError, match="uq_resource_grants_active"),
                connection.begin_nested(),
            ):
                connection.execute(insert_grant, duplicate)
            foreign = duplicate | {
                "id": uuid4(),
                "user": manager,
                "tenant": seed_company(connection).tenant_id,
            }
            with (
                pytest.raises(IntegrityError, match="fk_resource_grants_org_id_organizations"),
                connection.begin_nested(),
            ):
                connection.execute(insert_grant, foreign)

        command.downgrade(lane.config, "c01_authz_core")
        with lane.engine.connect() as connection:
            assert connection.execute(SELECT_GRANTS, {"items": list(items.values())}).all() == []
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM marketplace_item_acl WHERE item_id IN :items"
                ).bindparams(bindparam("items", expanding=True)),
                {"items": list(items.values())},
            ) == len(acl_rows)
        command.downgrade(lane.config, "b11_company_administration")
        with lane.engine.connect() as connection:
            assert not CORE_TABLES & set(inspect(connection).get_table_names())
        command.upgrade(lane.config, "head")
        with lane.engine.connect() as connection:
            # The chain grew past C-04; the roundtrip must land back on the current head.
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                alembic_head()
            )
