"""Keep an enrolled recipient eligible until their chat notification is committed."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext, active_memberships
from app.models.organization import Organization, OrganizationMember, Tenant
from app.models.user import User


async def lock_active_notice_recipient(
    db: AsyncSession, ctx: OrgContext, *, nowait: bool = True
) -> bool:
    """Scheduled deliveries retry a busy recipient; a one-shot worker can wait for the outcome."""
    membership = await db.scalar(
        active_memberships(ctx.user_id)
        .join(User, User.id == OrganizationMember.user_id)
        .where(
            User.is_active.is_(True),
            Organization.id == ctx.org_id,
            Organization.status == "active",
            Tenant.id == ctx.tenant_id,
            Tenant.status == "active",
        )
        .with_for_update(read=True, nowait=nowait)
    )
    return membership is not None
