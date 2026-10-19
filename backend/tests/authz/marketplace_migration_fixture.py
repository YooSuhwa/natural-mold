"""Exact expected grants for every legacy marketplace visibility and ACL level."""

from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from schema_drafts.org_authz.default_org_backfill import ORG, TENANT

type ExpectedGrant = tuple[UUID, str, str, UUID]


def seed_marketplace(connection: Connection, metadata: sa.MetaData) -> set[ExpectedGrant]:
    users = list(connection.scalars(sa.select(metadata.tables["users"].c.id)))
    owner = users[0]
    items, acl, orgs = (
        metadata.tables[name]
        for name in ("marketplace_items", "marketplace_item_acl", "organizations")
    )
    expected: set[ExpectedGrant] = set()
    for visibility in ("private", "restricted", "public", "unlisted", "system"):
        item = uuid4()
        system = visibility == "system"
        connection.execute(
            sa.insert(items).values(
                id=item,
                owner_user_id=None if system else owner,
                org_id=None if system else ORG,
                tenant_id=None if system else TENANT,
                resource_type="skill",
                name=visibility,
                slug=visibility,
                visibility=visibility,
                is_system=system,
                is_listed=visibility != "unlisted",
            )
        )
        if visibility == "restricted":
            for user, permission, relation in zip(
                users,
                ("view", "install", "manage"),
                ("viewer", "installer", "manager"),
                strict=False,
            ):
                connection.execute(
                    sa.insert(acl).values(item_id=item, user_id=user, permission=permission)
                )
                expected.add((item, relation, "user", user))
        elif visibility in ("public", "unlisted"):
            expected.add((item, "installer", "organization", ORG))
        elif system:
            expected.update(
                (item, "installer", "organization", org)
                for org in connection.scalars(sa.select(orgs.c.id))
            )
    return expected
