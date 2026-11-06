"""``GET /api/system/status``: this installation's components against its install profile.

Platform operators only (it reads every company's job counts and the worker heartbeats).
The answer names components and their state, never addresses with credentials or keys.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.policies import SUPER, authz_policy
from app.dependencies import CurrentUser, get_db, require_platform_operator
from app.install import status
from app.jobs import service as jobs
from app.schemas.system_status import (
    ComponentStatusOut,
    QueueOut,
    SystemStatusOut,
    WorkerOut,
)

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/status", response_model=SystemStatusOut)
@authz_policy(SUPER)
async def get_system_status(
    db: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(require_platform_operator),
) -> SystemStatusOut:
    found = await status.collect(db)
    limit = status.worker_silence_seconds()
    return SystemStatusOut(
        profile=found.profile,
        verdict=found.verdict,
        components=[ComponentStatusOut(**asdict(c)) for c in found.components],
        workers=[
            WorkerOut(
                worker_id=row.worker_id,
                hostname=row.hostname,
                job_types=list(row.job_types),
                started_at=row.started_at,
                heartbeat_at=row.heartbeat_at,
                live=jobs.heartbeat_age(row).total_seconds() <= limit,
            )
            for row in found.workers
        ],
        queue=QueueOut(**asdict(found.queue)),
        checked_at=found.checked_at,
    )
