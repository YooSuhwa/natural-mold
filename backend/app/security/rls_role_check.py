"""Start-up check for the RLS app role (plan 8.8, I-06).

With ``DB_RLS_ENABLED=true`` the ``DATABASE_URL`` role must not be able to
step around the m92 policies: not a superuser, not ``BYPASSRLS``, not the
owner of any Moldy table (an owner can ``ALTER TABLE ... DISABLE ROW LEVEL
SECURITY``), and every protected table must actually have RLS enabled and
forced. ``APP_ENV=production`` refuses to start on any issue; other
environments log warnings. Nothing runs when the flag is off or off PostgreSQL.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.database import platform_session
from app.migrations.org_authz_v1.rls_manifest import protected_tables

logger = logging.getLogger(__name__)

# Attributes are not inherited, but a member can SET ROLE to a bypassing role.
_ROLE = text(
    "SELECT coalesce(bool_or(rolsuper), false) AS rolsuper, "
    "coalesce(bool_or(rolbypassrls), false) AS rolbypassrls "
    "FROM pg_roles WHERE pg_has_role(current_user, oid, 'MEMBER')"
)
_TABLES = text(
    "SELECT c.relname, pg_has_role(current_user, c.relowner, 'MEMBER') AS owned, "
    "c.relrowsecurity AND c.relforcerowsecurity AS forced, "
    "EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid "
    "AND p.polname = 'tenant_boundary') AS policed "
    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'p')"
)


@dataclass(frozen=True, slots=True)
class RoleFacts:
    superuser: bool
    bypass_rls: bool
    owned: tuple[str, ...]
    unforced: tuple[str, ...]
    unpoliced: tuple[str, ...] = ()


def _sample(names: Iterable[str]) -> str:
    items = sorted(names)
    head = ", ".join(items[:5])
    return head + (f" (+{len(items) - 5} more)" if len(items) > 5 else "")


def rls_role_issues(facts: RoleFacts) -> list[str]:
    issues: list[str] = []
    if facts.superuser:
        issues.append(
            "DATABASE_URL role is (or is a member of) a superuser; superusers bypass RLS."
        )
    if facts.bypass_rls:
        issues.append(
            "DATABASE_URL role has (or is a member of a role with) BYPASSRLS; "
            "use a separate non-bypass app role."
        )
    if facts.owned:
        issues.append(
            "DATABASE_URL role owns (directly or by membership) Moldy tables ("
            + _sample(facts.owned)
            + "); run migrations as MIGRATION_DATABASE_URL and REASSIGN OWNED to it "
            "(docs/operator-setup.md section 8)."
        )
    if facts.unpoliced:
        issues.append(
            "The tenant_boundary policy is missing on " + _sample(facts.unpoliced) + "; run "
            "`python -m app.migrations.org_authz_v1.rls_activation enable` to install it."
        )
    if facts.unforced:
        issues.append(
            "RLS is not enabled and forced on " + _sample(facts.unforced) + "; apply m92 with "
            "DB_RLS_ENABLED=true or run `python -m app.migrations.org_authz_v1.rls_activation "
            "enable`."
        )
    return issues


async def read_role_facts(db: AsyncSession) -> RoleFacts:
    role = (await db.execute(_ROLE)).one()
    rows = (await db.execute(_TABLES)).all()
    protected = set(protected_tables(row.relname for row in rows))
    return RoleFacts(
        superuser=bool(role.rolsuper),
        bypass_rls=bool(role.rolbypassrls),
        owned=tuple(sorted(row.relname for row in rows if row.relname in protected and row.owned)),
        unforced=tuple(
            sorted(row.relname for row in rows if row.relname in protected and not row.forced)
        ),
        unpoliced=tuple(
            sorted(row.relname for row in rows if row.relname in protected and not row.policed)
        ),
    )


async def enforce_rls_role_safety(settings: Settings) -> None:
    if not settings.db_rls_enabled or not settings.database_url.startswith("postgresql"):
        return
    async with platform_session("startup.rls_role_check") as db:
        issues = rls_role_issues(await read_role_facts(db))
    if not issues:
        return
    if settings.app_env == "production":
        raise RuntimeError("Refusing to start with DB_RLS_ENABLED: " + " ".join(issues))
    for issue in issues:
        logger.warning("rls_role_check: %s", issue)
