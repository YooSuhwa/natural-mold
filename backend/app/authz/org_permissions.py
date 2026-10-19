"""Read-only summaries of DB role/capability sources for organization navigation."""

from typing import Final
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import OrganizationRoleGrant
from app.models.organization_group import Group, GroupMember, OrgCapabilityGrant
from app.schemas.organization import OrgCapability, OrgRole

BUILDER_CAPABILITIES: Final[tuple[OrgCapability, ...]] = (
    "cap_create_agent",
    "cap_create_skill",
    "cap_create_tool",
    "cap_register_mcp",
    "cap_publish_marketplace",
)
ADMIN_CAPABILITIES: Final[tuple[OrgCapability, ...]] = (
    *BUILDER_CAPABILITIES,
    "cap_connect_database",
    "cap_share_org_wide",
)
ROLE_ADAPTER: Final = TypeAdapter(tuple[OrgRole, ...])
CAPABILITY_ADAPTER: Final = TypeAdapter(tuple[OrgCapability, ...])


async def organization_permissions(
    db: AsyncSession, org_id: UUID, user_id: UUID
) -> tuple[tuple[OrgRole, ...], tuple[OrgCapability, ...]]:
    """Projection only; resource enforcement remains the authorization service's job."""
    groups = (
        select(GroupMember.group_id)
        .join(Group, (Group.id == GroupMember.group_id) & (Group.org_id == GroupMember.org_id))
        .where(GroupMember.org_id == org_id, GroupMember.user_id == user_id)
    )
    roles = ROLE_ADAPTER.validate_python(
        tuple(
            (
                await db.scalars(
                    select(OrganizationRoleGrant.role).where(
                        OrganizationRoleGrant.org_id == org_id,
                        or_(
                            (OrganizationRoleGrant.principal_type == "user")
                            & (OrganizationRoleGrant.principal_id == user_id),
                            (OrganizationRoleGrant.principal_type == "group")
                            & OrganizationRoleGrant.principal_id.in_(groups),
                        ),
                    )
                )
            ).all()
        )
    )
    direct = CAPABILITY_ADAPTER.validate_python(
        tuple(
            (
                await db.scalars(
                    select(OrgCapabilityGrant.capability).where(
                        OrgCapabilityGrant.org_id == org_id,
                        or_(
                            (OrgCapabilityGrant.principal_type == "user")
                            & (OrgCapabilityGrant.principal_id == user_id),
                            (OrgCapabilityGrant.principal_type == "group")
                            & OrgCapabilityGrant.principal_id.in_(groups),
                            (OrgCapabilityGrant.principal_type == "organization")
                            & (OrgCapabilityGrant.principal_id == org_id),
                        ),
                    )
                )
            ).all()
        )
    )
    capabilities = set(direct)
    if "admin" in roles:
        capabilities.update(ADMIN_CAPABILITIES)
    elif "builder" in roles:
        capabilities.update(BUILDER_CAPABILITIES)
    return tuple(sorted(set(roles))), tuple(sorted(capabilities))
