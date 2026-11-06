"""A requester's background jobs (app/jobs): progress, a progress stream, cancel.

A job is visible only to the person who asked for it, in the organization it belongs to;
anyone else gets the same 404 as for a job that does not exist.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Final
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.policies import SELF, authz_policy
from app.database import tenant_session
from app.dependencies import get_db, get_org_context, verify_csrf
from app.jobs import service
from app.schemas.jobs import JobOut

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_POLL_SECONDS: Final = 1.0
_STREAM_LIMIT_SECONDS: Final = 600


@router.get("/{job_id}", response_model=JobOut)
@authz_policy(SELF)
async def get_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
) -> JobOut:
    return JobOut.model_validate(await service.owned_job(db, job_id, context), from_attributes=True)


@router.get("/{job_id}/events")
@authz_policy(SELF)
async def stream_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
) -> StreamingResponse:
    """Server-sent ``job`` events whenever the job changes, until it ends (or 10 minutes)."""
    await service.owned_job(db, job_id, context)  # 404 before the stream starts

    async def events() -> AsyncIterator[str]:
        last: str | None = None
        for _ in range(int(_STREAM_LIMIT_SECONDS / _POLL_SECONDS)):
            async with tenant_session(context) as session:
                job = await service.owned_job(session, job_id, context)
                view = JobOut.model_validate(job, from_attributes=True)
            encoded = view.model_dump_json()
            if encoded != last:
                last = encoded
                yield f"event: job\ndata: {encoded}\n\n"
            if service.is_finished(view.state):
                return
            await asyncio.sleep(_POLL_SECONDS)
        yield f"event: timeout\ndata: {json.dumps({'id': str(job_id)})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@router.post("/{job_id}/cancel", response_model=JobOut)
@authz_policy(SELF)
async def cancel_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
    context: OrgContext = Depends(get_org_context),
) -> JobOut:
    job = await service.request_cancel(db, await service.owned_job(db, job_id, context))
    await db.commit()
    await db.refresh(job)
    return JobOut.model_validate(job, from_attributes=True)
