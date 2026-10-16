"""Route-facing resource checks that keep the existing loader as the legacy answer.

Plan 8.4 and 9.5 (D-04). Each check runs the route's existing owner-scoped
loader first; its result is the legacy verdict, so ``AUTHZ_MODE=legacy`` keeps
the same queries, status codes and bodies (an existing 404 is re-raised as the
same object). ``shadow`` answers the same way and records a sample; only then,
when the loader found nothing, is the row read by id to give the sample its
organization. ``enforce`` answers with OpenFGA: hidden or foreign rows are the
resource's existing 404, visible rows without the permission are 403
``permission_required``.

Platform catalog rows (``org_id``/``tenant_id`` NULL: system tools, LLM models) and
same-company rows without an organization are decided in the caller's organization; OpenFGA's
``in_boundary``/allowed-org fanout decides whether that organization may use them.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.principal import Principal
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.authz.service_types import Verdict
from app.dependencies import CurrentUser, get_current_user, get_db, get_org_context
from app.error_codes import credential_not_found, mcp_server_not_found, model_not_found
from app.error_codes import tool_not_found as _tool_not_found
from app.exceptions import AppError
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.model import Model
from app.models.tool import Tool


@dataclass(frozen=True, slots=True)
class GatedResource[T]:
    fga_type: str
    model: type[T]
    min_visible: str
    not_found: Callable[[], AppError]
    # Whether platform rows (system, org/tenant NULL) are reachable on this route family.
    platform: bool = True


TOOL = GatedResource("tool", Tool, "can_use", _tool_not_found)
# System MCP servers stay unreachable (legacy parity) until catalog fanout (E-03).
MCP_SERVER = GatedResource("mcp_server", McpServer, "can_use", mcp_server_not_found, False)
# System credentials stay behind the SUPER system-credential routes.
CREDENTIAL = GatedResource("credential", Credential, "can_use", credential_not_found, False)
LLM_MODEL = GatedResource("llm_model", Model, "can_use", model_not_found)


def _missing(error: Exception) -> bool:
    status = error.status if isinstance(error, AppError) else getattr(error, "status_code", None)
    return isinstance(error, AppError | HTTPException) and status == 404


def resource_org(row: object | None, ctx: OrgContext, *, platform: bool = True) -> UUID | None:
    """The organization a decision is made in; None means missing or foreign."""
    if row is None or (not platform and getattr(row, "is_system", False)):
        return None
    org_id = getattr(row, "org_id", None)
    tenant_id = getattr(row, "tenant_id", None)
    if tenant_id is not None and tenant_id != ctx.tenant_id:
        return None
    # Platform and company rows (no organization) reach the caller's organization only
    # through OpenFGA's allowed_org fanout (in_boundary), so decide them there.
    return ctx.org_id if org_id is None else org_id


async def gate_row[T](
    authz: AuthzService,
    db: AsyncSession,
    ctx: OrgContext,
    spec: GatedResource[T],
    row_id: UUID,
    permission: str,
    legacy_load: Callable[[], Awaitable[T | None]],
) -> T:
    """Load ``row_id`` for ``permission``; the legacy loader's 404 is the legacy denial."""
    missing: Exception | None = None
    try:
        row = await legacy_load()
    except (AppError, HTTPException) as error:
        if not _missing(error):
            raise
        row, missing = None, error
    target = row
    if target is None and authz.mode != "legacy":
        target = await db.get(spec.model, row_id)
    try:
        await authz.require(
            Principal.for_user(ctx),
            permission,
            f"{spec.fga_type}:{row_id}",
            min_visible=spec.min_visible,
            resource_org_id=resource_org(target, ctx, platform=spec.platform),
            legacy=Verdict.ALLOW if row is not None else Verdict.NOT_FOUND,
            not_found=spec.not_found(),
        )
    except AppError as denied:
        if denied.status == 404 and missing is not None:
            raise missing from None
        raise
    if target is None:  # unreachable: require only allows a located row
        raise spec.not_found()
    return target


async def gate_unchecked[T](
    authz: AuthzService,
    db: AsyncSession,
    ctx: OrgContext,
    spec: GatedResource[T],
    row_id: UUID,
    permission: str,
) -> None:
    """A route without a legacy check (legacy allow): shadow samples it, enforce decides.

    Fixing the legacy gap itself is D-10; only the enforce answer is 8.4 here.
    """
    if authz.mode == "legacy":
        return
    target = await db.get(spec.model, row_id)
    await authz.require(
        Principal.for_user(ctx),
        permission,
        f"{spec.fga_type}:{row_id}",
        min_visible=spec.min_visible,
        resource_org_id=resource_org(target, ctx, platform=spec.platform),
        legacy=Verdict.ALLOW,
        not_found=spec.not_found(),
    )


async def gate_list[R](
    authz: AuthzService,
    ctx: OrgContext,
    fga_type: str,
    permission: str,
    rows: Sequence[R],
    row_id: Callable[[R], UUID],
    *,
    candidates: Callable[[], Awaitable[Sequence[R]]] | None = None,
) -> list[R]:
    """Legacy rows answer unless enforced; enforce confirms candidates with BatchCheck.

    Shadow samples the candidate page too, so rows the legacy query hides but a
    grant would show are recorded as differences.
    """
    p = Principal.for_user(ctx)

    def objects(page: Sequence[R]) -> list[str]:
        return [f"{fga_type}:{row_id(row)}" for row in page]

    if not authz.enforces(fga_type):
        pool = await candidates() if candidates and authz.mode == "shadow" else rows
        await authz.filter_visible(p, permission, objects(pool), legacy=objects(rows))
        return list(rows)
    pool = await candidates() if candidates else rows
    kept = set(await authz.filter_visible(p, permission, objects(pool), legacy=[]))
    return [row for row in pool if f"{fga_type}:{row_id(row)}" in kept]


async def gate_cap(authz: AuthzService, ctx: OrgContext, capability: str) -> None:
    """Every current-organization member may create today (legacy allow)."""
    await authz.require_cap(ctx, capability, legacy=True)


async def org_context_when_consulted(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    authz: AuthzService = Depends(get_authz),
) -> OrgContext | None:
    """Routes that never resolved an organization stay org-free unless models are enforced.

    Legacy never resolves one. Shadow (or enforce of other types) tolerates a caller
    without an organization: the legacy answer stands and nothing is sampled.
    """
    if authz.mode == "legacy":
        return None
    try:
        return await get_org_context(request, user, db)
    except (AppError, HTTPException):
        if authz.enforces("llm_model"):
            raise
        return None


def require_same_org(authz: AuthzService, credential: Credential, target_org: UUID | None) -> None:
    """Enforced: a credential bound to a target must live in the target's organization."""
    if not authz.enforces("credential") or credential.org_id is None:
        return
    if credential.org_id != target_org:
        raise credential_not_found()
