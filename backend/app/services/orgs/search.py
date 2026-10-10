"""Search only the current organization and disclose emails only to member managers."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_control import OrgControlContext, OrgControlTarget, load_org_control
from app.authz.org_search_queries import group_search_query, user_search_query
from app.schemas.org_search import PrincipalSearchQuery, PrincipalSearchView


async def search_principals(
    db: AsyncSession, context: OrgControlContext, query: PrincipalSearchQuery
) -> list[PrincipalSearchView]:
    context = await load_org_control(db, context.actor, OrgControlTarget(context.organization.id))
    requested = set(query.types.split(","))
    results: list[PrincipalSearchView] = []
    if "user" in requested:
        for user_id, name, display_name, email in (
            await db.execute(user_search_query(context, query))
        ).tuples():
            results.append(
                PrincipalSearchView(
                    type="user",
                    id=user_id,
                    display_name=display_name or name,
                    email=email if "can_manage_members" in context.permissions else None,
                )
            )
    if "group" in requested and len(results) < 20:
        for group_id, name, count in (
            await db.execute(group_search_query(context, query).limit(20 - len(results)))
        ).tuples():
            results.append(
                PrincipalSearchView(
                    type="group", id=group_id, display_name=name, member_count=count
                )
            )
    organization = context.organization
    if (
        "org" in requested
        and len(results) < 20
        and query.q.casefold() in organization.name.casefold()
    ):
        results.append(
            PrincipalSearchView(type="org", id=organization.id, display_name=organization.name)
        )
    return results
