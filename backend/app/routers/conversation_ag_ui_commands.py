"""Standard AG-UI POST for the authenticated conversation actor."""

from __future__ import annotations

from uuid import UUID

from ag_ui.core import RunAgentInput, UserMessage
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_agent_checks import bind_request_authz
from app.authz.org_context import OrgContext
from app.authz.policies import PRIVATE, authz_policy
from app.authz.service import AuthzService
from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_db,
    get_org_context,
    owned_conversation,
    verify_csrf,
)
from app.models.conversation import Conversation
from app.services.ag_ui_start_input import start_message_id
from app.services.ag_ui_submission import submit_ag_ui_run

router = APIRouter(tags=["conversations"])


async def ag_ui_run_response(
    request: Request, context: OrgContext, conversation_id: UUID, public_run_id: str
) -> StreamingResponse:
    from app.services.ag_ui_read import ag_ui_run_response as read_run

    return await read_run(request, context, conversation_id, public_run_id)


@router.post(
    "/api/conversations/{conversation_id}/ag-ui/runs",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def post_ag_ui_run(
    conversation_id: UUID,
    run_input: RunAgentInput,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    conversation: Conversation = Depends(owned_conversation),
    _csrf: None = Depends(verify_csrf),
    _authz: AuthzService = Depends(bind_request_authz),
) -> StreamingResponse:
    binding = await submit_ag_ui_run(
        db,
        request=request,
        context=context,
        user=user,
        conversation=conversation,
        run_input=run_input,
        wire_input=await request.json(),
    )
    response = await ag_ui_run_response(request, context, conversation_id, binding.public_run_id)
    last = run_input.messages[-1] if run_input.messages else None
    props = run_input.forwarded_props
    controls = props.get("moldy") if isinstance(props, dict) else None
    operation = controls.get("operation", "send") if isinstance(controls, dict) else "send"
    if not run_input.resume and operation in ("send", "edit") and isinstance(last, UserMessage):
        response.headers["X-Moldy-Message-Id"] = start_message_id(
            conversation_id, run_input.run_id, last.id
        )
    return response
