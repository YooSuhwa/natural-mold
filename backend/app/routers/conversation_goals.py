"""Private ordinary-chat goals and their immutable, authenticated result versions."""

from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_agent_checks import bind_request_authz, require_agent_run
from app.authz.conversation_scope import lock_private_conversation, require_private_conversation
from app.authz.org_context import OrgContext
from app.authz.policies import PRIVATE, authz_policy
from app.authz.service import AuthzService
from app.dependencies import get_db, get_org_context, verify_csrf
from app.error_codes import file_not_found
from app.schemas.conversation_goal import (
    GoalActionRequest,
    GoalResponse,
    GoalWriteRequest,
)
from app.services import artifact_service
from app.services.artifact_service import ArtifactNotFoundError
from app.services.conversation_goal_evidence import (
    read_goal_evidence_version,
    verified_goal_history_run,
)
from app.services.conversation_goal_service import (
    apply_goal_action,
    get_goal,
    goal_response,
    ordinary_conversation,
    save_goal,
)
from app.services.conversation_history_evidence import RunHistoryReference, run_history_reference
from app.services.conversation_run_queue_service import require_queue_executable

router = APIRouter(prefix="/api/conversations/{conversation_id}/goal", tags=["conversation-goals"])


async def _require_goal_execution(
    db: AsyncSession, conversation_id: UUID, context: OrgContext, authz: AuthzService
) -> None:
    agent_id = await require_queue_executable(db, conversation_id, context)
    await require_agent_run(context, agent_id, authz=authz)


@router.get("", response_model=GoalResponse)
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def read_conversation_goal(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
) -> GoalResponse:
    conversation = await require_private_conversation(db, conversation_id, context)
    return goal_response(conversation, await get_goal(db, conversation))


@router.put("", response_model=GoalResponse, dependencies=[Depends(verify_csrf)])
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def put_conversation_goal(
    conversation_id: UUID,
    payload: GoalWriteRequest,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(bind_request_authz),
) -> GoalResponse:
    await _require_goal_execution(db, conversation_id, context, authz)
    conversation = await lock_private_conversation(db, conversation_id, context)
    result = goal_response(conversation, await save_goal(db, conversation, payload))
    await db.commit()
    return result


@router.post("/actions", response_model=GoalResponse, dependencies=[Depends(verify_csrf)])
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def change_conversation_goal_state(
    conversation_id: UUID,
    payload: GoalActionRequest,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(bind_request_authz),
) -> GoalResponse:
    if payload.action == "resume":
        await _require_goal_execution(db, conversation_id, context, authz)
    conversation = await lock_private_conversation(db, conversation_id, context)
    result = goal_response(conversation, await apply_goal_action(db, conversation, payload))
    await db.commit()
    return result


@router.get("/evidence/{version_id}/history-reference", response_model=RunHistoryReference | None)
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def read_goal_history_reference(
    conversation_id: UUID,
    version_id: UUID,
    goal_id: UUID,
    goal_version: Annotated[int, Query(ge=1)],
    response: Response,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
) -> RunHistoryReference | None:
    response.headers["Cache-Control"] = "private, no-store"
    conversation = await require_private_conversation(db, conversation_id, context)
    goal = await get_goal(db, conversation)
    if (
        not ordinary_conversation(conversation)
        or goal is None
        or goal.id != goal_id
        or goal.version != goal_version
    ):
        return None
    source_run_id = await verified_goal_history_run(db, context, goal, version_id)
    if source_run_id is None:
        return None
    return await run_history_reference(db, conversation, context, source_run_id)


@router.get("/artifacts/{artifact_id}/versions/{version_id}/download")
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def download_goal_evidence(
    conversation_id: UUID,
    artifact_id: UUID,
    version_id: UUID,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
) -> Response:
    await require_private_conversation(db, conversation_id, context)
    try:
        version, content = await read_goal_evidence_version(
            db,
            context=context,
            conversation_id=conversation_id,
            artifact_id=artifact_id,
            version_id=version_id,
        )
        await artifact_service.record_artifact_download(
            db, context=context, artifact_id=artifact_id
        )
        await db.commit()
    except ArtifactNotFoundError as exc:
        raise file_not_found() from exc
    filename = quote(version.original_filename, safe="")
    return Response(
        content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + filename,
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
