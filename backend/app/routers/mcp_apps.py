"""Authenticated, run-scoped MCP Apps RemoteHost command endpoint."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.schemas.mcp_apps import (
    McpAppsCallToolCommand,
    McpAppsCommand,
    McpAppsListResourcesCommand,
    McpAppsReadResourceCommand,
)
from app.services import mcp_apps_service
from app.services.audit_service import record_self_event

router = APIRouter(tags=["mcp-apps"])


@router.post(
    "/api/conversations/{conversation_id}/runs/{run_id}/mcp-apps/{tool_call_id}",
)
async def execute_mcp_apps_command(
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    tool_call_id: Annotated[str, Path(min_length=1, max_length=255)],
    command: McpAppsCommand,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> dict[str, Any]:
    try:
        context = await mcp_apps_service.authorize_binding(
            db,
            conversation_id=conversation_id,
            run_id=run_id,
            tool_call_id=tool_call_id,
            user_id=user.id,
        )
        match command:
            case McpAppsReadResourceCommand():
                _verify_params_context(
                    context.binding.mcp_server_id,
                    context.binding.resource_uri,
                    server_id=command.params.server_id,
                    resource_uri=command.params.uri,
                )
                result = await mcp_apps_service.read_bound_resource(db, context)
                if command.method == "resources/read":
                    result = {
                        "contents": [
                            {
                                "uri": result["uri"],
                                "mimeType": result["mimeType"],
                                "text": result["html"],
                                "_meta": {"ui": result["meta"]},
                            }
                        ]
                    }
            case McpAppsListResourcesCommand():
                _verify_params_context(
                    context.binding.mcp_server_id,
                    context.binding.resource_uri,
                    server_id=command.params.server_id,
                )
                result = await mcp_apps_service.list_bound_resources(context)
            case McpAppsCallToolCommand():
                _verify_params_context(
                    context.binding.mcp_server_id,
                    context.binding.resource_uri,
                    server_id=command.params.server_id,
                )
                result = await mcp_apps_service.call_bound_tool(
                    db,
                    context,
                    tool_name=command.params.name,
                    arguments=command.params.arguments,
                )
        await record_self_event(
            db,
            user,
            action=f"mcp_app.{command.method.replace('/', '.')}",
            target_type="mcp_app_invocation_binding",
            target_id=context.binding.id,
            request=request,
            run_id=run_id,
            metadata={
                "method": command.method,
                "mcp_server_id": str(context.binding.mcp_server_id),
                "mcp_tool_id": str(context.binding.mcp_tool_id),
            },
        )
        await db.commit()
        return result
    except mcp_apps_service.McpAppsError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


def _verify_params_context(
    bound_server_id: uuid.UUID,
    bound_resource_uri: str,
    *,
    server_id: str | None,
    resource_uri: str | None = None,
) -> None:
    if server_id is not None and server_id != str(bound_server_id):
        raise mcp_apps_service.McpAppsError(
            "mcp_app_server_mismatch", "server does not match the bound context", 403
        )
    if resource_uri is not None and resource_uri != bound_resource_uri:
        raise mcp_apps_service.McpAppsError(
            "mcp_app_resource_mismatch", "resource does not match the bound context", 403
        )


__all__ = ["router"]
