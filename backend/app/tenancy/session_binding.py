"""I-06: bind request sessions that never pass through ``get_org_context``.

Routes without an organization context (company/organization administration,
personal pages, API keys, public share links) resolve their company from the
caller, the key or the token. ``bind_company_once`` then binds the still
unbound request session to that company, exactly like ``get_org_context`` does
for organization routes. A session that is already bound keeps its scope, so
with ``DB_RLS_ENABLED`` off nothing but ``session.info`` changes.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SCOPE_INFO_KEY, SessionTenant, bind_tenant


def is_unbound(db: AsyncSession) -> bool:
    info = getattr(db, "info", None)  # test doubles may not carry a dict
    return isinstance(info, dict) and info.get(SCOPE_INFO_KEY) is None


async def bind_company_once(db: AsyncSession, tenant_id: UUID, user_id: UUID | None) -> None:
    """Bind an unbound root session to one company (no organization attribution).

    Organization attribution (audit ``org_id``) stays with ``get_org_context``;
    a nested transaction cannot bind and stays unbound (fails closed under RLS).
    """
    if is_unbound(db) and not db.in_nested_transaction():
        await bind_tenant(db, SessionTenant(tenant_id, user_id))
