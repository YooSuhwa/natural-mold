"""Display names of grant principals (S12 role grants, S16 access list).

A user is shown by their profile name (``display_name``), falling back to the account
``name`` — the same rule as the member directory and the notification bell, so a renamed
user never shows a stale registration name. Groups and organizations use their name.
"""

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.organization_group import Group
from app.models.user import User


async def user_display_names(db: AsyncSession, ids: set[UUID]) -> dict[UUID, str]:
    if not ids:
        return {}
    name = func.coalesce(User.display_name, User.name)
    rows = await db.execute(select(User.id, name).where(User.id.in_(ids)))
    return dict(rows.tuples().all())


async def principal_names(
    db: AsyncSession, principals: Iterable[tuple[str, UUID]]
) -> dict[tuple[str, UUID], str]:
    """``(type, id) → name`` for ``user``, ``group`` and ``organization`` principals."""
    wanted: dict[str, set[UUID]] = {"user": set(), "group": set(), "organization": set()}
    for kind, pid in principals:
        wanted.setdefault(kind, set()).add(pid)
    found: dict[tuple[str, UUID], str] = {
        ("user", pid): name for pid, name in (await user_display_names(db, wanted["user"])).items()
    }
    for kind, model in (("group", Group), ("organization", Organization)):
        if wanted[kind]:
            rows = await db.execute(select(model.id, model.name).where(model.id.in_(wanted[kind])))
            found.update({(kind, pid): name for pid, name in rows.tuples().all()})
    return found
