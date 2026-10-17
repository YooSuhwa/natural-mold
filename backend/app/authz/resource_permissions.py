"""``my_permissions`` for tool, MCP server, credential and skill rows (plan 13.1, 13.9; H-05).

The list/detail bodies of these resources carry no per-row permissions, so the screens
ask once per page: ``GET /api/resources/{type}/permissions?ids=…``. Only rows of the
caller's current organization (platform/company rows are decided there, like
``resource_gate``) that pass the list's ``can_use`` confirmation are answered; every
other id is left out, so the answer never reveals a hidden row (8.4, 8.10).

Legacy answer: the owner holds every permission, platform rows (``is_system``) may be
used and viewed, anything else is invisible — the same rule the legacy lists apply.
"""

from collections.abc import Callable, Sequence
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import denials
from app.authz.client import AuthzUnavailable
from app.authz.org_context import OrgContext
from app.authz.principal import Principal
from app.authz.resource_gate import resource_org
from app.authz.service import AuthzService
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.skill import Skill
from app.models.tool import Tool

PermissionRow = Tool | McpServer | Credential | Skill

_SHAREABLE: Final = ("can_use", "can_view", "can_edit", "can_share", "can_delete")
# model.fga vocabulary per type (credentials have no separate ``can_view``).
PERMISSIONS: Final[dict[str, tuple[str, ...]]] = {
    "tool": _SHAREABLE,
    "mcp_server": _SHAREABLE,
    "skill": _SHAREABLE,
    "credential": ("can_use", "can_edit", "can_share", "can_delete"),
}
MODELS: Final[dict[str, type[PermissionRow]]] = {
    "tool": Tool,
    "mcp_server": McpServer,
    "skill": Skill,
    "credential": Credential,
}
_PLATFORM_LEGACY: Final = frozenset({"can_use", "can_view"})
MAX_IDS: Final = 100
VISIBLE: Final = "can_use"
# Shadow samples per request while the type is not enforced.
SHADOW_SAMPLE_ROWS: Final = 3


def _platform(kind: str) -> bool:
    # System credentials stay behind the SUPER system-credential routes.
    return kind != "credential"


def legacy_permissions(row: PermissionRow, ctx: OrgContext, perms: Sequence[str]) -> list[str]:
    if row.user_id == ctx.user_id and row.org_id == ctx.org_id:
        return list(perms)
    if getattr(row, "is_system", False):
        return [perm for perm in perms if perm in _PLATFORM_LEGACY]
    return []


async def _rows(
    db: AsyncSession, ctx: OrgContext, kind: str, ids: Sequence[UUID]
) -> list[PermissionRow]:
    model = MODELS[kind]
    found = await db.scalars(select(model).where(model.id.in_(list(dict.fromkeys(ids)))))
    platform = _platform(kind)
    return [row for row in found if resource_org(row, ctx, platform=platform) == ctx.org_id]


async def resource_permissions(
    db: AsyncSession, authz: AuthzService, ctx: OrgContext, kind: str, ids: Sequence[UUID]
) -> list[tuple[UUID, list[str]]]:
    """``(id, my_permissions)`` for the visible rows among ``ids``, in request order."""
    perms = list(PERMISSIONS[kind])
    rows = {row.id: row for row in await _rows(db, ctx, kind, ids)}
    ordered = [rows[i] for i in dict.fromkeys(ids) if i in rows]
    p = Principal.for_user(ctx)

    def obj(row: PermissionRow) -> str:
        return f"{kind}:{row.id}"

    if not authz.enforces(kind):
        return await _legacy_answer(authz, p, ctx, ordered, perms, obj)
    kept = set(await authz.filter_visible(p, VISIBLE, [obj(r) for r in ordered], legacy=[]))
    visible = [row for row in ordered if obj(row) in kept]
    if not visible:
        return []
    try:
        answers = await authz.batch_check(p, [(perm, obj(r)) for r in visible for perm in perms])
    except AuthzUnavailable as error:
        raise denials.authz_unavailable() from error
    return [(row.id, [perm for perm in perms if answers[(perm, obj(row))]]) for row in visible]


async def _legacy_answer(
    authz: AuthzService,
    p: Principal,
    ctx: OrgContext,
    rows: Sequence[PermissionRow],
    perms: list[str],
    obj: Callable[[PermissionRow], str],
) -> list[tuple[UUID, list[str]]]:
    """Not enforced: the legacy rule answers; shadow samples only the first rows (the
    list pages already sample visibility, and a full page would be ~600 samples)."""
    answered = [(row, legacy_permissions(row, ctx, perms)) for row in rows]
    visible = [(row, held) for row, held in answered if VISIBLE in held]
    sample = rows[:SHADOW_SAMPLE_ROWS]
    sampled_visible = [obj(r) for r, held in answered[:SHADOW_SAMPLE_ROWS] if VISIBLE in held]
    await authz.filter_visible(p, VISIBLE, [obj(r) for r in sample], legacy=sampled_visible)
    for row, held in visible[:SHADOW_SAMPLE_ROWS]:
        await authz.my_permissions(p, obj(row), perms, legacy=held)
    return [(row.id, held) for row, held in visible]
