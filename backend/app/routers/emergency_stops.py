"""Emergency stop API (feature plan v2 순번 13, 17.4). Every route is ``SELF``; who may
stop or release is checked against the target (``app/approvals/emergency.py``).

* ``POST /api/emergency-stops``: stop an agent, a person's agents or an MCP server.
* ``GET /api/emergency-stops?active=true``: a company admin sees every stop, others the
  stops they made or that concern them.
* ``POST /api/emergency-stops/{id}/release``: with a reason.
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import emergency
from app.authz.org_context import OrgContext
from app.authz.policies import SELF, authz_policy
from app.dependencies import get_db, get_org_context, verify_csrf
from app.schemas.emergency_stop import (
    EmergencyStopCreate,
    EmergencyStopPage,
    EmergencyStopRelease,
    EmergencyStopState,
    EmergencyStopView,
)

router = APIRouter(prefix="/api/emergency-stops", tags=["emergency-stops"])


async def _view(db: AsyncSession, row: emergency.EmergencyStop) -> EmergencyStopView:
    return EmergencyStopView(
        id=row.id,
        scope_type=row.scope_type,
        scope_id=row.scope_id,
        scope_name=row.scope_name,
        status=row.status,
        reason=row.reason,
        stopped_by=row.stopped_by,
        stopped_at=row.stopped_at,
        released_by=row.released_by,
        released_at=row.released_at,
        release_reason=row.release_reason,
        state=EmergencyStopState(**await emergency.state(db, row)),
    )


@router.post("", response_model=EmergencyStopView, status_code=201)
@authz_policy(SELF)
async def create_stop(
    body: EmergencyStopCreate,
    db: AsyncSession = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
    context: OrgContext = Depends(get_org_context),
) -> EmergencyStopView:
    row = await emergency.stop(
        db, context, scope_type=body.scope_type, scope_id=body.scope_id, reason=body.reason
    )
    return await _view(db, row)


@router.get("", response_model=EmergencyStopPage)
@authz_policy(SELF)
async def list_stops(
    active: bool = True,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
) -> EmergencyStopPage:
    rows = await emergency.visible_stops(db, context, active_only=active)
    return EmergencyStopPage(items=[await _view(db, row) for row in rows])


@router.post("/{stop_id}/release", response_model=EmergencyStopView)
@authz_policy(SELF)
async def release_stop(
    stop_id: UUID,
    body: EmergencyStopRelease,
    db: AsyncSession = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
    context: OrgContext = Depends(get_org_context),
) -> EmergencyStopView:
    row = await emergency.release(db, context, stop_id=stop_id, reason=body.reason)
    return await _view(db, row)
