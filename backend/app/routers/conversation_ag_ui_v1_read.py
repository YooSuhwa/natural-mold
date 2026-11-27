"""Authenticated AG-UI state and committed event recovery; all routes are read-only."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.policies import PRIVATE, authz_policy
from app.database import tenant_session
from app.dependencies import get_current_user, get_db, get_org_context
from app.schemas.ag_ui_snapshot import AgUiSnapshot
from app.services import conversation_stream_service
from app.services.ag_ui_read import ag_ui_run_response, read_frame_page
from app.services.ag_ui_read_access import require_read_conversation
from app.services.ag_ui_read_target import resolve_read_target
from app.services.ag_ui_snapshot import load_ag_ui_snapshot

_SSE_RESPONSE: dict[int | str, dict[str, Any]] = {
    200: {"description": "Committed AG-UI lifecycle", "content": {"text/event-stream": {}}}
}

router = APIRouter(prefix="/api/conversations/{conversation_id}/ag-ui", tags=["conversations"])


async def _stream_context(
    request: Request, db: AsyncSession = Depends(get_db, scope="function")
) -> OrgContext:
    # Release the initial authentication connection before streaming. Long-lived
    # subscribers resolve permissions using independent short transactions.
    user = await get_current_user(request, db)
    return await get_org_context(request, user, db)


@router.get("/snapshot", response_model=AgUiSnapshot)
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def read_snapshot(
    request: Request,
    response: Response,
    conversation_id: uuid.UUID,
    public_id: str | None = Query(None, alias="runId", min_length=1, max_length=200),
    context: OrgContext = Depends(get_org_context),
) -> AgUiSnapshot:
    response.headers["Cache-Control"] = "no-store"
    async with tenant_session(context, maker=conversation_stream_service.async_session) as db:
        conversation = await require_read_conversation(
            db, request=request, context=context, conversation_id=conversation_id
        )
        target = (
            await resolve_read_target(db, conversation, context, public_id) if public_id else None
        )
        snapshot = await load_ag_ui_snapshot(
            db,
            conversation,
            context,
            run_id=target.run.id if target and target.run else None,
            public_run_id=public_id,
        )
        # A large history may outlive the incoming credential.
        await require_read_conversation(
            db, request=request, context=context, conversation_id=conversation_id
        )
        return snapshot


@router.get("/stream", response_class=StreamingResponse, responses=_SSE_RESPONSE)
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def attach_by_query(
    request: Request,
    conversation_id: uuid.UUID,
    public_run_id: str = Query(..., alias="runId", min_length=1, max_length=200),
    after: str | None = Query(None, max_length=200),
    last_event_id: str | None = Header(None, alias="Last-Event-ID", max_length=200),
    context: OrgContext = Depends(_stream_context),
) -> StreamingResponse:
    return await ag_ui_run_response(
        request, context, conversation_id, public_run_id, after=after or last_event_id
    )


@router.get(
    "/runs/{public_run_id:path}/stream", response_class=StreamingResponse, responses=_SSE_RESPONSE
)
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def attach_by_path(
    request: Request,
    conversation_id: uuid.UUID,
    public_run_id: str,
    after: str | None = Query(None, max_length=200),
    last_event_id: str | None = Header(None, alias="Last-Event-ID", max_length=200),
    context: OrgContext = Depends(_stream_context),
) -> StreamingResponse:
    return await ag_ui_run_response(
        request, context, conversation_id, public_run_id, after=after or last_event_id
    )


@router.get("/events")
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def read_events_by_query(
    request: Request,
    conversation_id: uuid.UUID,
    response: Response,
    public_run_id: str = Query(..., alias="runId", min_length=1, max_length=200),
    after: str | None = Query(None, max_length=200),
    limit: int = Query(64, ge=1, le=256),
    context: OrgContext = Depends(get_org_context),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await read_frame_page(
        request, context, conversation_id, public_run_id, after=after, limit=limit
    )


@router.get("/runs/{public_run_id:path}/events")
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def read_events_by_path(
    request: Request,
    conversation_id: uuid.UUID,
    public_run_id: str,
    response: Response,
    after: str | None = Query(None, max_length=200),
    limit: int = Query(64, ge=1, le=256),
    context: OrgContext = Depends(get_org_context),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await read_frame_page(
        request, context, conversation_id, public_run_id, after=after, limit=limit
    )
