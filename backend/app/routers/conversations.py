from __future__ import annotations

from fastapi import APIRouter

from app.routers import (
    conversation_ag_ui,
    conversation_agent_protocol,
    conversation_agent_protocol_sdk,
    conversation_branches,
    conversation_crud,
    conversation_files,
    conversation_followup,
    conversation_messages,
    conversation_runs,
    conversation_traces,
)

router = APIRouter(tags=["conversations"])

router.include_router(conversation_crud.router)
router.include_router(conversation_traces.router)
router.include_router(conversation_runs.router)
router.include_router(conversation_ag_ui.router)
router.include_router(conversation_agent_protocol.router)
router.include_router(conversation_agent_protocol_sdk.router)
router.include_router(conversation_messages.router)
router.include_router(conversation_branches.router)
router.include_router(conversation_files.router)
router.include_router(conversation_followup.router)
