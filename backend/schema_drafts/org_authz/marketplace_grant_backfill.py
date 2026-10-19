"""m85 exact ACL and visibility-to-grant projection; no OpenFGA writes here."""

from typing import Literal, assert_never
from uuid import NAMESPACE_URL, UUID, uuid5

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict
from sqlalchemy.engine import Connection

from schema_drafts.org_authz.schema import schema_metadata


class ItemSource(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    org_id: UUID | None
    tenant_id: UUID | None
    visibility: Literal["private", "restricted", "public", "unlisted", "system"]


class AclSource(BaseModel):
    user_id: UUID
    permission: Literal["view", "install", "manage"]


def relation(permission: Literal["view", "install", "manage"]) -> str:
    match permission:
        case "view":
            return "viewer"
        case "install":
            return "installer"
        case "manage":
            return "manager"
        case _:
            assert_never(permission)


def copy_grants(connection: Connection) -> None:
    metadata = schema_metadata(scope_columns=True)
    grants, items, acl, orgs = (
        metadata.tables[name]
        for name in (
            "resource_grants",
            "marketplace_items",
            "marketplace_item_acl",
            "organizations",
        )
    )
    sources = connection.execute(
        sa.select(items.c.id, items.c.org_id, items.c.tenant_id, items.c.visibility)
    ).mappings()
    for item in (ItemSource.model_validate(raw) for raw in sources):
        targets: list[tuple[UUID, UUID, str, UUID, str]] = []
        match item.visibility:
            case "private":
                continue
            case "restricted":
                if item.org_id is None or item.tenant_id is None:
                    raise ValueError(f"Restricted marketplace item has no scope: {item.id}")
                rows = connection.execute(
                    sa.select(acl.c.user_id, acl.c.permission).where(acl.c.item_id == item.id)
                ).mappings()
                for entry in (AclSource.model_validate(raw) for raw in rows):
                    targets.append(
                        (
                            item.org_id,
                            item.tenant_id,
                            "user",
                            entry.user_id,
                            relation(entry.permission),
                        )
                    )
            case "public" | "unlisted":
                if item.org_id is None or item.tenant_id is None:
                    raise ValueError(f"Public marketplace item has no scope: {item.id}")
                targets.append(
                    (item.org_id, item.tenant_id, "organization", item.org_id, "installer")
                )
            case "system":
                targets.extend(
                    (org, tenant, "organization", org, "installer")
                    for org, tenant in connection.execute(
                        sa.select(orgs.c.id, orgs.c.tenant_id)
                    ).all()
                )
            case _:
                assert_never(item.visibility)
        for org, tenant, principal_type, principal, permission in targets:
            identity = uuid5(
                NAMESPACE_URL, f"moldy/m85/{item.id}/{permission}/{principal_type}/{principal}"
            )
            existing = connection.scalar(
                sa.select(grants.c.id).where(
                    grants.c.resource_type == "marketplace_item",
                    grants.c.resource_id == item.id,
                    grants.c.relation == permission,
                    grants.c.principal_type == principal_type,
                    grants.c.principal_id == principal,
                )
            )
            if existing is None:
                connection.execute(
                    sa.insert(grants).values(
                        id=identity,
                        org_id=org,
                        tenant_id=tenant,
                        resource_type="marketplace_item",
                        resource_id=item.id,
                        relation=permission,
                        principal_type=principal_type,
                        principal_id=principal,
                        grant_kind="migration",
                        source_link={"migration": "m85"},
                    )
                )


def reverse_grants(connection: Connection) -> None:
    grants = schema_metadata().tables["resource_grants"]
    connection.execute(
        sa.delete(grants).where(
            grants.c.grant_kind == "migration",
            grants.c.source_link["migration"].as_string() == "m85",
        )
    )
