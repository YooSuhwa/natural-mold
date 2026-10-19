"""C-01 (plan m85): copy marketplace ACL and visibility into resource_grants (10.5).

``marketplace_item_acl`` stays the legacy source until I-04 drops it. Mapping:

* restricted: one user grant per ACL row (view/install/manage -> viewer/installer/manager).
* public, unlisted: ``organization:<item org>`` installer.
* system: installer fanout to every organization. These rows also use
  ``grant_kind=migration`` (not ``system_fanout``); D-05 catalog on/off writes must match them
  on relation/principal, not on grant_kind.
* private: none, ACL rows included (owner only; a manager grant would imply viewer/installer).
* public, unlisted: legacy ``can_manage_item`` honours ``manage`` ACL rows, so those rows also
  become manager grants (deviation, see progress C-01); view/install rows add nothing beyond
  the visibility rule and are not copied.

Restricted ACL users with no active membership in the item's company are still copied (exact
legacy access) and counted in a warning; reconcile/D-05 decides their fate.

Grant ids are deterministic and existing active keys are skipped, so reruns are identical.
OpenFGA tuples are never written here (``authz bootstrap`` projects them after deploy).
"""

from __future__ import annotations

import logging
from uuid import NAMESPACE_URL, UUID, uuid5

import sqlalchemy as sa

from alembic import op

revision = "c01_marketplace_acl_grants"
down_revision = "c01_authz_core"
branch_labels = None
depends_on = None

_RELATION = {"view": "viewer", "install": "installer", "manage": "manager"}
_log = logging.getLogger("alembic.runtime.migration")

_items = sa.table(
    "marketplace_items",
    sa.column("id", sa.Uuid()),
    sa.column("org_id", sa.Uuid()),
    sa.column("tenant_id", sa.Uuid()),
    sa.column("visibility", sa.String()),
)
_acl = sa.table(
    "marketplace_item_acl",
    sa.column("item_id", sa.Uuid()),
    sa.column("user_id", sa.Uuid()),
    sa.column("permission", sa.String()),
)
_orgs = sa.table("organizations", sa.column("id", sa.Uuid()), sa.column("tenant_id", sa.Uuid()))
_members = sa.table(
    "tenant_members",
    sa.column("tenant_id", sa.Uuid()),
    sa.column("user_id", sa.Uuid()),
    sa.column("status", sa.String()),
)
_grants = sa.table(
    "resource_grants",
    sa.column("id", sa.Uuid()),
    sa.column("org_id", sa.Uuid()),
    sa.column("tenant_id", sa.Uuid()),
    sa.column("state", sa.String()),
    sa.column("resource_type", sa.String()),
    sa.column("resource_id", sa.Uuid()),
    sa.column("relation", sa.String()),
    sa.column("principal_type", sa.String()),
    sa.column("principal_id", sa.Uuid()),
    sa.column("grant_kind", sa.String()),
    sa.column("source_link", sa.JSON()),
)

type Target = tuple[UUID, UUID, str, UUID, str]


def _scoped(item: sa.Row[tuple[UUID, UUID | None, UUID | None, str]]) -> tuple[UUID, UUID]:
    if item.org_id is None or item.tenant_id is None:
        raise ValueError(f"{item.visibility} marketplace item has no organization: {item.id}")
    return item.org_id, item.tenant_id


def _targets(connection: sa.Connection) -> list[tuple[UUID, Target]]:
    orgs = connection.execute(sa.select(_orgs.c.id, _orgs.c.tenant_id)).all()
    items = connection.execute(
        sa.select(_items.c.id, _items.c.org_id, _items.c.tenant_id, _items.c.visibility)
    ).all()
    targets: list[tuple[UUID, Target]] = []
    for item in items:
        acl = connection.execute(
            sa.select(_acl.c.user_id, _acl.c.permission).where(_acl.c.item_id == item.id)
        ).all()
        if item.visibility == "system":
            if any(entry.permission == "manage" for entry in acl):
                raise ValueError(
                    f"system marketplace item {item.id} has a 'manage' ACL row; delete that "
                    "marketplace_item_acl row or change the item visibility, then rerun"
                )
            targets += [
                (item.id, (org, tenant, "organization", org, "installer")) for org, tenant in orgs
            ]
            continue
        org, tenant = _scoped(item)
        if item.visibility == "private":
            continue
        copied = (
            acl if item.visibility == "restricted" else [e for e in acl if e.permission == "manage"]
        )
        targets += [
            (item.id, (org, tenant, "user", entry.user_id, _RELATION[entry.permission]))
            for entry in copied
        ]
        if item.visibility in ("public", "unlisted"):
            targets.append((item.id, (org, tenant, "organization", org, "installer")))
    return targets


def _cross_company_acl_users(connection: sa.Connection) -> int:
    member = sa.exists().where(
        _members.c.tenant_id == _items.c.tenant_id,
        _members.c.user_id == _acl.c.user_id,
        _members.c.status == "active",
    )
    query = (
        sa.select(sa.func.count())
        .select_from(_acl.join(_items, _items.c.id == _acl.c.item_id))
        .where(_items.c.visibility == "restricted", ~member)
    )
    return connection.scalar(query) or 0


def upgrade() -> None:
    connection = op.get_bind()
    outsiders = _cross_company_acl_users(connection)
    if outsiders:
        _log.warning(
            "m85 copied %d restricted ACL rows whose user is not an active member of the "
            "item's company; reconcile must review them",
            outsiders,
        )
    for item_id, (org, tenant, principal_type, principal, relation) in _targets(connection):
        existing = connection.scalar(
            sa.select(_grants.c.id).where(
                _grants.c.state == "active",
                _grants.c.resource_type == "marketplace_item",
                _grants.c.resource_id == item_id,
                _grants.c.relation == relation,
                _grants.c.principal_type == principal_type,
                _grants.c.principal_id == principal,
            )
        )
        if existing is not None:
            continue
        key = f"moldy/m85/{item_id}/{relation}/{principal_type}/{principal}"
        connection.execute(
            sa.insert(_grants).values(
                id=uuid5(NAMESPACE_URL, key),
                org_id=org,
                tenant_id=tenant,
                state="active",
                resource_type="marketplace_item",
                resource_id=item_id,
                relation=relation,
                principal_type=principal_type,
                principal_id=principal,
                grant_kind="migration",
                source_link={"migration": "m85"},
            )
        )


def downgrade() -> None:
    # Only this revision writes migration-kind marketplace grants; the ACL was never touched.
    op.get_bind().execute(
        sa.delete(_grants).where(
            _grants.c.resource_type == "marketplace_item", _grants.c.grant_kind == "migration"
        )
    )
