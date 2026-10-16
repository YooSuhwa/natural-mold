"""Shadow-mode disagreement rows (plan 6.2 ``authz_shadow_diffs``, 8.5).

A sample disagrees when its legacy and OpenFGA verdicts differ. The table keeps
booleans, so a disagreement is expressed on the relation where the answers part:
the requested permission when allow/deny differs, otherwise the ``min_visible``
relation when only 404/403 differs (visible vs. not visible).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.service_types import ShadowSample, Verdict
from app.database import platform_sessions
from app.models.authz import AuthzShadowDiff

# Column widths of ``authz_shadow_diffs`` (route, principal/object, permission, request_id).
ROUTE_MAX, NAME_MAX, PERMISSION_MAX, REQUEST_ID_MAX = 255, 100, 80, 100
KEY_COLUMNS = ("route", "principal", "object", "permission", "legacy", "fga")


@dataclass(frozen=True, slots=True, order=True)
class DiffKey:
    """``uq_authz_shadow_diffs_key``."""

    route: str
    principal: str
    object: str
    permission: str
    legacy: bool
    fga: bool


@dataclass(slots=True)
class DiffTally:
    count: int
    request_id: str | None


def diff_key(sample: ShadowSample, fga: Verdict, route: str) -> DiffKey | None:
    """The aggregation key of a disagreement; ``None`` when both answers agree."""
    legacy = sample.legacy
    if legacy is fga:
        return None
    permission = sample.permission
    legacy_bit, fga_bit = legacy is Verdict.ALLOW, fga is Verdict.ALLOW
    if legacy_bit == fga_bit:  # 404 vs 403: the visibility relation disagrees
        permission = sample.min_visible or permission
        legacy_bit, fga_bit = legacy is not Verdict.NOT_FOUND, fga is not Verdict.NOT_FOUND
    return DiffKey(
        route[:ROUTE_MAX],
        sample.principal.fga()[:NAME_MAX],
        sample.obj[:NAME_MAX],
        permission[:PERMISSION_MAX],
        legacy_bit,
        fga_bit,
    )


def _insert(dialect: str) -> Callable[..., Any]:
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert


def diff_rows(tallies: Mapping[DiffKey, DiffTally]) -> list[dict[str, Any]]:
    """Insert rows sorted by key, so concurrent writers take row locks in one order."""
    return [
        {
            "id": uuid4(),
            "route": key.route,
            "principal": key.principal,
            "object": key.object,
            "permission": key.permission,
            "legacy": key.legacy,
            "fga": key.fga,
            "request_id": tally.request_id and tally.request_id[:REQUEST_ID_MAX],
            "count": tally.count,
        }
        for key, tally in sorted(tallies.items(), key=lambda kv: kv[0])
    ]


async def upsert_diffs(db: AsyncSession, tallies: Mapping[DiffKey, DiffTally]) -> None:
    """Insert new keys or add to ``count``; the latest request id wins."""
    if not tallies:
        return
    dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
    stmt = _insert(dialect)(AuthzShadowDiff).values(diff_rows(tallies))
    stmt = stmt.on_conflict_do_update(
        index_elements=list(KEY_COLUMNS),
        set_={
            "count": AuthzShadowDiff.count + stmt.excluded.count,
            "request_id": stmt.excluded.request_id,
        },
    )
    await db.execute(stmt)


def diff_sessions(maker: Callable[[], AsyncSession] | None = None) -> Callable[[], AsyncSession]:
    """Platform scope: shadow diffs are instance-wide observation rows (no tenant_id)."""
    if maker is None:
        return platform_sessions("authz.shadow_diffs")
    return platform_sessions("authz.shadow_diffs", maker=maker)
