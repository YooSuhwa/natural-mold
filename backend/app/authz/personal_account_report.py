"""Read-only report of share grants that 10.4 now forbids (E-05).

Grants written before E-05 may share a personal account credential or a resource that
lends one. Nothing is revoked automatically: an operator reviews the list
(``python -m app.authz.cli personal-account-shares``) and the owners revoke or change
the resources.
"""

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.personal_accounts import SHARE_KINDS, SHARE_PRINCIPALS, personal_accounts
from app.models.agent import Agent
from app.models.authz import ResourceGrant
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.tool import Tool

_TYPES = {"agent": Agent, "credential": Credential, "tool": Tool, "mcp_server": McpServer}


class PersonalAccountShare(BaseModel):
    grant_id: str
    org_id: str
    resource_type: str
    resource_id: str
    principal_type: str
    principal_id: str
    level: str
    personal_accounts: list[dict[str, str]]


async def personal_account_shares(db: AsyncSession) -> list[PersonalAccountShare]:
    grants = (
        await db.scalars(
            select(ResourceGrant)
            .where(
                ResourceGrant.state == "active",
                ResourceGrant.grant_kind.in_(SHARE_KINDS),
                ResourceGrant.principal_type.in_(SHARE_PRINCIPALS),
                ResourceGrant.resource_type.in_(tuple(_TYPES)),
            )
            .order_by(ResourceGrant.resource_type, ResourceGrant.resource_id, ResourceGrant.id)
        )
    ).all()
    found: list[PersonalAccountShare] = []
    checked: dict[tuple[str, object], list[dict[str, str]]] = {}
    for grant in grants:
        key = (grant.resource_type, grant.resource_id)
        if key not in checked:
            row = await db.get(_TYPES[grant.resource_type], grant.resource_id)
            checked[key] = [] if row is None else await personal_accounts(db, row)
        if checked[key]:
            found.append(
                PersonalAccountShare(
                    grant_id=str(grant.id),
                    org_id=str(grant.org_id),
                    resource_type=grant.resource_type,
                    resource_id=str(grant.resource_id),
                    principal_type=grant.principal_type,
                    principal_id=str(grant.principal_id),
                    level=grant.relation,
                    personal_accounts=checked[key],
                )
            )
    return found
